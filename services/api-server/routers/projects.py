from fastapi import APIRouter, HTTPException, File, UploadFile, Header, Request, Response, Query
from fastapi.encoders import jsonable_encoder
from starlette.datastructures import Headers
from starlette.concurrency import run_in_threadpool
from typing import Dict, Any
from db.repositories.request_project_repository import current_project_scope
from use_cases.cash_flow_dependencies import dependent_inputs, financial_input_hash
from use_cases.intake_transfers import IntakeTransferStore, processing_succeeded
from core.cash_flow_periods import require_stress_calendar_period
from core.financial_input_validation import FinancialInputError
from schemas.report import ReportCashFlowBasis, ReportCalculationSelection
from use_cases.report_calculation_basis import (
    CALCULATION_SLOTS, calculation_choice, freeze_cash_flow_basis, freeze_calculation_basis,
)
from use_cases.stored_permit_evidence import resolve_stored_permit_documents
from use_cases.land_legal_preview import compact_land_legal_review, compact_review_hash
from datetime import datetime, timezone
import base64
import tempfile
import os
import re
from urllib.parse import quote
from uuid import uuid4
from time import monotonic
from agents.ai_connection import resolve_ai_context
from agents.feasibility_workbook_importer import import_feasibility_study_from_workbook
from agents.feasibility_workbook_validator import validate_feasibility_workbook
from agents.parser_agent import PARSER_AGENT_VERSION, ParserAgent
from agents.parser_provenance import attach_parser_field_provenance
from agents.risk_report_workbook_importer import import_risk_report_workbook
from agents.report_export_agent import ReportExportAgent

from core.authz import (
    authorize_request,
    enforce_high_risk_step_up,
    enforce_project_tenant_scope,
    identity_context_from_request,
    is_explicit_human_principal,
)
from core.credential_session import credential_session_binding_from_request
from core.calculations import (
    calculate_cashflow_max_minus,
    build_monthly_cash_flow,
    calculate_pf_metrics,
    calculate_feasibility_metrics,
    calculate_return_metrics,
    normalize_tranche_priority,
)
from core.risk_assessment import build_basic_risk_matrix
from schemas.scenario import ScenarioSetRequest
from schemas.stress_test import StressTestRequest, StressTranche, StressScenarioDriver, StressTestSchedule
from agents.scenario_engine_agent import ScenarioEngineAgent
from agents.stress_test_agent import DEFAULT_STRESS_SCENARIOS, StressTestAgent
from use_cases.pf_structure import build_pf_structure_input, execute_pf_structure_input
from schemas.credit_enhancement import CreditEnhancementRequest
from agents.credit_enhancement_agent import CreditEnhancementAgent
from schemas.cash_sweep import CashSweepRequest
from schemas.cashflow_export import (
    CASHFLOW_EXPORT_ENGINE_KEYS,
    CashflowExportCompletionRequest,
)
from schemas.document_review_workspace import DocumentReviewWorkspaceViewModel
from agents.cash_sweep_agent import CashSweepAgent
from integrations.kakao import build_kakao_intake_plan_message, build_kakao_workflow_result_message
from agents.restructuring_agent import RestructuringAgent
from db.repositories.market_evidence_repository import MarketEvidenceRepository
from db.repositories.calculation_run_repository import (
    CalculationRunRepositoryError,
    PostgresCalculationRunRepository,
    select_local_export_runs,
)
from db.repositories.orchestration_repository import (
    OrchestrationRepository,
    PostgresOrchestrationRepository,
)
from db.repositories.project_repository import ProjectRepository
from db.repositories.postgres_project_repository import (
    PostgresProjectRepository,
    ProjectVersionConflict,
)
from db.repositories.recent_activity_repository import (
    PostgresRecentActivityRepository,
    RecentActivityRepositoryError,
)
from db.repositories.project_snapshot_repository import (
    PostgresProjectSnapshotRepository,
    ProjectSnapshotRepository,
)
from db.repositories.report_repository import (
    PostgresReportRepository,
    ReportPublicationConflict,
    ReportRepository,
)
from db.repositories.source_document_repository import SourceDocumentRepository
from db.repositories.activity_log_repository import PostgresActivityLogRepository
from db.repositories.upload_archive_repository import UploadArchiveRepository, UploadRejectedError
from db.repositories.legal_intelligence_repository import (
    LegalReviewDecisionConflict,
    PostgresLegalTenantOverlayRepository,
)
from db.repositories.legal_review_projection_repository import (
    PostgresLegalReviewProjectionRepository,
)
from db.legal_review_database import (
    connect_legal_review_database,
    legal_review_database_configured,
)
from db.postgres import postgres_configured
from storage import storage_backend
from storage import (
    get_evidence_graph_record,
    get_chief_pod_artifact_record,
    get_pod_ai_review_record,
    get_prompt_artifact_record,
    list_chief_pod_artifact_records,
    list_pod_ai_review_records,
    list_recalculation_event_records,
    get_risk_register_record,
    list_evidence_graph_records,
    list_assumption_override_records,
    list_land_evidence_records,
    list_prompt_artifact_records,
    list_restructuring_cure_records,
    list_risk_register_records,
    list_calculation_run_records,
    list_deal_state_records,
    _deal_state_record_has_source_locator,
    upsert_deal_state_record,
    load_state,
    record_user_activity_log,
    list_user_activity_logs,
    save_state,
    upsert_project_record,
    delete_project_record_cascade,
    upsert_orchestration_run,
    upsert_report_record,
    upsert_market_evidence_records,
    upsert_project_snapshot_record,
    upsert_assumption_override_record,
    upsert_land_evidence_record,
    upsert_chief_pod_artifact_record,
    upsert_pod_ai_review_record,
    upsert_agent_handoff_record,
    list_agent_handoff_records,
    get_agent_run_record,
    list_agent_run_records,
    list_agent_run_step_records,
    list_brain_usage_event_records,
    upsert_recalculation_event_record,
    upsert_prompt_artifact_record,
    upsert_restructuring_cure_record,
    upsert_risk_register_record,
    upsert_calculation_run_record,
    upsert_legal_review_record,
)
from use_cases.cash_flow import build_cash_flow_request_use_case, execute_cash_flow_request_use_case
from agent_platform.runtime import StorageAgentRunRepository
from agent_platform.contracts import LearningCandidate
from agent_platform.learning import LearningLifecycleService
from agent_platform.operational import OperationalRepository
from use_cases.chief_deal_orchestration import (
    plan_chief_deal_orchestration_use_case,
    run_chief_deal_all_pods_use_case,
    run_chief_deal_pod_use_case,
    run_chief_deal_pod_with_cascade_use_case,
)
from use_cases.common import deep_merge
from use_cases.development_feasibility import analyze_development_feasibility_use_case, development_feasibility_input_basis
from use_cases.asset_feasibility_comparison import compare_asset_feasibility_use_case
from use_cases.covenant_eod import evaluate_covenant_eod_use_case
from use_cases.product_planning import compare_product_planning_use_case
from use_cases.market_product_depth import analyze_market_product_depth_use_case
from use_cases.reanalyze_on_evidence import reanalyze_after_evidence_use_case
from use_cases.feasibility_input_builder import build_feasibility_input_draft, canonical_product_mix_to_runtime
from use_cases.evidence_review import list_market_evidence_use_case, review_market_evidence_use_case
from use_cases.excel_workbook_learning import learn_excel_workbook_mapping_use_case
from agents.land_ledger_agent import LAND_LEDGER_AGENT_VERSION, LandLedgerParseError
from use_cases.land_ledger_import import analyze_land_ledger_workbook_use_case, enrich_land_ledger_use_case, reconcile_land_costs_use_case
from use_cases.feasibility_workbook_llm import (
    FeasibilityReviewConflict,
    apply_feasibility_input_use_case,
    feasibility_review_state,
    extract_feasibility_fields_via_llm,
)
from use_cases.intake_finalize import finalize_project_intake_use_case
from use_cases.parcel_pnu_resolution import (
    canonical_project_parcel_rows,
    parcel_address_lookup_mismatch,
    parcel_lookup_address,
    parcel_pnu,
    resolve_address_to_parcel,
    resolve_project_parcels_use_case,
)
from use_cases.auto_evidence_supplement import auto_supplement_validation_gaps
from use_cases.parsed_facts import parsed_fact_package, persist_parsed_facts
from use_cases.parsed_fact_validation import validate_parsed_facts_for_pod
from use_cases.calculation_run_capture import scenario_engine_run_payloads, stress_test_run_payload
from use_cases.legal_review import review_legal_and_permits_use_case
from use_cases.legal_pod import run_legal_pod_use_case
from use_cases.legal_review_decisions import decide_legal_review_use_case
from use_cases.legal_intelligence import seal_legal_review_evidence_snapshot
from use_cases.legal_review_projection import (
    mark_projection_persistence_failure,
    persist_legal_pod_projections,
)
from use_cases.marketability_review import analyze_marketability_use_case
from use_cases.orchestration_approval import approve_orchestration_run_by_id_use_case, review_orchestration_handoff_by_id_use_case, review_risk_register_row_by_id_use_case
from use_cases.pf_review import run_and_save_pf_review_use_case, plan_orchestration_use_case, run_workflow_use_case
from routers.project_jobs import build_project_jobs_router
from routers.project_parser_v2 import build_project_parser_v2_router
from routers.project_source_documents import build_project_source_documents_router
from use_cases.document_review_workspace import (
    build_document_review_workspace_read_model,
)
from use_cases.glossary import list_glossary_candidates_use_case
from use_cases.parser_v2_records import (
    ParserV2RecordStore,
    approved_input_snapshot_wire_payload,
)
from use_cases.parser_v2_runtime import build_parser_v2_runtime_dependencies
from use_cases.term_annotation import annotate_terms_use_case
from use_cases.user_ai_profile import build_user_ai_context, safe_user_ai_context_summary
from use_cases.covenant_monitoring import (
    monitor_covenants_use_case,
    set_monitoring_thresholds_use_case,
)
from use_cases.portfolio_monitoring import build_portfolio_monitoring
from use_cases.map_intelligence import build_project_map_intelligence, project_map_parcels, resolve_and_attach_geometry
from integrations.vworld import fetch_parcel_geometry_by_pnu, vworld_configured, vworld_status
from integrations.market_data import MolitRtmsConnector
from agents.document_classifier_agent import (
    classify_document, intake_processing_types, read_intake_pdf_sample, sniff_intake_format,
)
from parsing_platform.adapters import inspect_office_container_safety
from agents.intake_triage_agent import triage_intake_documents
from use_cases.market_comparables import (
    fetch_project_market_comparables,
    inject_market_comparables,
    recent_deal_yms,
)
from use_cases.developability_score import score_developability_use_case
from use_cases.project_snapshots import (
    diff_project_snapshots_use_case,
    get_project_snapshot_use_case,
    list_project_snapshots_use_case,
    search_similar_project_snapshots_use_case,
    upsert_project_snapshot_outcome_use_case,
)
from use_cases.project_management import (
    apply_parsed_project_data_use_case,
    create_project_use_case,
    delete_project_use_case,
    get_project_use_case,
    list_projects_use_case,
    update_project_use_case,
)
from use_cases.report_generation import (
    export_saved_report_use_case,
    generate_and_save_pf_report_use_case,
    publish_saved_report_use_case,
)
from use_cases.source_locator_completeness import run_source_locator_completeness_audit_use_case
from use_cases.report_templates import (
    get_report_template_use_case,
    list_report_templates_use_case,
    upsert_report_template_use_case,
)
from use_cases.source_documents import (
    archive_source_document_use_case,
    delete_project_upload_archive_use_case,
    list_source_documents_use_case,
    save_source_document_metadata_use_case,
)
from use_cases.workbook_import_approval import (
    approve_feasibility_workbook_import_use_case,
    approve_risk_report_workbook_import_use_case,
    feasibility_study_draft_hash,
    persist_feasibility_workbook_candidate_use_case,
)

router = APIRouter(prefix="/projects", tags=["projects"])

# Workspace-level monitoring (spans all projects). Kept on its own prefix so the
# portfolio route does not collide with /projects/{project_id} path matching.
monitoring_router = APIRouter(prefix="/monitoring", tags=["monitoring"])

import json

PROJECTS_DB = {}
ORCHESTRATIONS_DB = {}
REPORTS_DB = {}
MARKET_EVIDENCE_DB = {}
PROJECT_SNAPSHOTS_DB = {}
project_id_counter = 1
PARSER_V2_RUNTIME_DEPENDENCIES: Dict[str, Any] = {}


def _project_store():
    if storage_backend() in {"postgres", "postgresql"}:
        scope = current_project_scope()
        if scope is None:
            raise RuntimeError("A verified request/job scope is required for project access")
        return scope
    return PROJECTS_DB

def persist_state() -> None:
    if storage_backend() in {"postgres", "postgresql"}:
        raise RuntimeError("Global state saves are disabled for normalized PostgreSQL")
    save_state(
        _project_store(),
        REPORTS_DB,
        ORCHESTRATIONS_DB,
        project_id_counter,
        MARKET_EVIDENCE_DB,
        PROJECT_SNAPSHOTS_DB,
    )

def allocate_project_id() -> str:
    global project_id_counter
    project_id = str(project_id_counter)
    project_id_counter += 1
    return project_id

def persist_project(project_id: str) -> None:
    """Incrementally persist a single project (or cascade-delete if removed),
    falling back to a full save_state() on the json backend."""
    if storage_backend() in {"postgres", "postgresql"}:
        _project_store().persist(project_id)
        return
    payload = _project_store().get(str(project_id))
    if payload is None:
        if not delete_project_record_cascade(project_id):
            persist_state()
        return
    if not upsert_project_record(project_id, payload, project_id_counter):
        persist_state()

def persist_orchestration_run(project_id: str, run_id: str) -> None:
    """Incrementally persist a single orchestration run, with full-save fallback."""
    run = (ORCHESTRATIONS_DB.get(str(project_id)) or {}).get(str(run_id))
    if run is None or not upsert_orchestration_run(project_id, run_id, run):
        persist_state()

def persist_report(project_id: str, record: Dict[str, Any]) -> None:
    """Incrementally persist a single report record, with full-save fallback."""
    if not upsert_report_record(project_id, record):
        persist_state()

def persist_snapshot(project_id: str, record: Dict[str, Any]) -> None:
    """Incrementally persist a single project snapshot, with full-save fallback."""
    if not upsert_project_snapshot_record(project_id, record):
        persist_state()


def persist_market_evidence(project_id: str, records: list[Dict[str, Any]]) -> None:
    if not upsert_market_evidence_records(project_id, records):
        persist_state()

def project_repository(
    request: Request | None = None,
    *,
    tenant_id: str | None = None,
):
    """Return the configured project authority.

    PostgreSQL is authoritative whenever the normalized Postgres backend is
    selected.  The in-memory repository remains a deliberate local/test lane;
    it is never used as a fallback after an authenticated production request.
    """
    if storage_backend() in {"postgres", "postgresql"}:
        scope = current_project_scope()
        if request is None and scope is not None:
            if tenant_id and tenant_id != scope.tenant_id:
                raise HTTPException(status_code=403, detail={"error": "tenant_scope_mismatch"})
            return scope
        resolved_tenant_id = str(tenant_id or "").strip()
        if request is not None:
            context = identity_context_from_request(request)
            request_tenant_id = str(
                (context.get("user") or {}).get("tenantId") or ""
            ).strip()
            if resolved_tenant_id and request_tenant_id != resolved_tenant_id:
                raise HTTPException(
                    status_code=403,
                    detail={"error": "tenant_scope_mismatch"},
                )
            resolved_tenant_id = request_tenant_id
        if not resolved_tenant_id:
            if request is not None:
                raise HTTPException(
                    status_code=401,
                    detail={"error": "tenant_required"},
                )
            raise RuntimeError(
                "A verified tenant is required for normalized project persistence"
            )
        return PostgresProjectRepository(tenant_id=resolved_tenant_id)
    return ProjectRepository(
        _project_store(),
        REPORTS_DB,
        ORCHESTRATIONS_DB,
        MARKET_EVIDENCE_DB,
        PROJECT_SNAPSHOTS_DB,
        persist_state,
        allocate_project_id,
        persist_project=persist_project,
    )

def report_repository(
    request: Request | None = None,
    *,
    tenant_id: str | None = None,
):
    """Return PostgreSQL authority or the explicit local compatibility lane."""
    if storage_backend() in {"postgres", "postgresql"}:
        if request is None and current_project_scope() is not None:
            scope_tenant = current_project_scope().tenant_id
            if tenant_id and tenant_id != scope_tenant:
                raise HTTPException(status_code=403, detail={"error": "tenant_scope_mismatch"})
            tenant_id = scope_tenant
        resolved_tenant_id = str(tenant_id or "").strip()
        if request is not None:
            context = identity_context_from_request(request)
            request_tenant_id = str(
                (context.get("user") or {}).get("tenantId") or ""
            ).strip()
            if resolved_tenant_id and request_tenant_id != resolved_tenant_id:
                raise HTTPException(
                    status_code=403,
                    detail={"error": "tenant_scope_mismatch"},
                )
            resolved_tenant_id = request_tenant_id
        if not resolved_tenant_id:
            if request is not None:
                raise HTTPException(
                    status_code=401,
                    detail={"error": "tenant_required"},
                )
            raise RuntimeError(
                "A verified tenant is required for normalized report persistence"
            )
        return PostgresReportRepository(tenant_id=resolved_tenant_id)
    return ReportRepository(REPORTS_DB, persist_state, persist_report=persist_report)

def source_document_repository() -> SourceDocumentRepository:
    return SourceDocumentRepository(_project_store(), persist_state, persist_project=persist_project)


def project_source_document_read_repository(
    project_id: str,
    project: Dict[str, Any],
) -> SourceDocumentRepository:
    """Create a read boundary over one already-authorized project record.

    In PostgreSQL mode the record came from the request tenant's normalized
    repository.  Keeping this one-record store prevents a later source lookup
    from silently falling back to the legacy process-global projection.
    """

    return SourceDocumentRepository({str(project_id): project}, lambda: None)

def upload_archive_repository() -> UploadArchiveRepository:
    return UploadArchiveRepository()


def _project_tenant_id(project_id: str) -> str | None:
    project = _project_store().get(str(project_id))
    if not isinstance(project, dict):
        return None
    metadata = (
        project.get("_meta")
        if isinstance(project.get("_meta"), dict)
        else {}
    )
    value = (
        project.get("tenantId")
        or project.get("tenant_id")
        or metadata.get("tenantId")
        or metadata.get("tenant_id")
    )
    normalized = str(value or "").strip()
    return normalized or None


def recent_project_activity_repository(
    *,
    tenant_id: str,
    user_id: str,
) -> PostgresRecentActivityRepository:
    """Composition point for the normalized recent-work activity projection."""

    return PostgresRecentActivityRepository(
        tenant_id=tenant_id,
        user_id=user_id,
    )


def _tenant_scoped_snapshot_corpus(
    project_id: str,
    snapshots: list[Dict[str, Any]],
) -> list[Dict[str, Any]]:
    """Exclude snapshots whose project lineage cannot be tied to this tenant."""
    expected_tenant_id = _project_tenant_id(project_id)
    scoped: list[Dict[str, Any]] = []
    for snapshot in snapshots:
        candidate_project_id = str(snapshot.get("projectId") or "").strip()
        canonical_project = (
            snapshot.get("canonicalProject")
            if isinstance(snapshot.get("canonicalProject"), dict)
            else {}
        )
        metadata = (
            canonical_project.get("_meta")
            if isinstance(canonical_project.get("_meta"), dict)
            else {}
        )
        candidate_tenant_id = str(
            snapshot.get("tenantId")
            or snapshot.get("tenant_id")
            or metadata.get("tenantId")
            or metadata.get("tenant_id")
            or _project_tenant_id(candidate_project_id)
            or ""
        ).strip()
        if expected_tenant_id:
            if candidate_tenant_id == expected_tenant_id:
                scoped.append(snapshot)
        elif candidate_project_id == str(project_id):
            # Legacy tenantless data may only compare within the current project.
            scoped.append(snapshot)
    return scoped

def parser_v2_record_store() -> ParserV2RecordStore:
    factory = PARSER_V2_RUNTIME_DEPENDENCIES.get("record_store_factory")
    return factory() if callable(factory) else ParserV2RecordStore()

def market_evidence_repository() -> MarketEvidenceRepository:
    return MarketEvidenceRepository(MARKET_EVIDENCE_DB, persist_state, persist_market_evidence)

def project_snapshot_repository(
    request: Request | None = None,
    *,
    tenant_id: str | None = None,
    local_compatibility: bool = False,
):
    if (
        storage_backend() in {"postgres", "postgresql"}
        and not local_compatibility
    ):
        if request is None and current_project_scope() is not None:
            scope_tenant = current_project_scope().tenant_id
            if tenant_id and tenant_id != scope_tenant:
                raise HTTPException(status_code=403, detail={"error": "tenant_scope_mismatch"})
            tenant_id = scope_tenant
        resolved_tenant_id = str(tenant_id or "").strip()
        if request is not None:
            context = identity_context_from_request(request)
            request_tenant_id = str(
                (context.get("user") or {}).get("tenantId") or ""
            ).strip()
            if resolved_tenant_id and request_tenant_id != resolved_tenant_id:
                raise HTTPException(
                    status_code=403,
                    detail={"error": "tenant_scope_mismatch"},
                )
            resolved_tenant_id = request_tenant_id
        if not resolved_tenant_id:
            if request is not None:
                raise HTTPException(
                    status_code=401,
                    detail={"error": "tenant_required"},
                )
            raise RuntimeError(
                "A verified tenant is required for normalized Snapshot persistence"
            )
        return PostgresProjectSnapshotRepository(
            tenant_id=resolved_tenant_id
        )
    return ProjectSnapshotRepository(PROJECT_SNAPSHOTS_DB, persist_state, persist_snapshot=persist_snapshot)

def orchestration_repository(
    request: Request | None = None,
    *,
    tenant_id: str | None = None,
) -> OrchestrationRepository:
    if storage_backend() in {"postgres", "postgresql"}:
        if request is None and current_project_scope() is not None:
            scope_tenant = current_project_scope().tenant_id
            if tenant_id and tenant_id != scope_tenant:
                raise HTTPException(status_code=403, detail={"error": "tenant_scope_mismatch"})
            tenant_id = scope_tenant
        resolved_tenant_id = str(tenant_id or "").strip()
        if request is not None:
            context = identity_context_from_request(request)
            request_tenant_id = str(
                (context.get("user") or {}).get("tenantId") or ""
            ).strip()
            if resolved_tenant_id and request_tenant_id != resolved_tenant_id:
                raise HTTPException(
                    status_code=403,
                    detail={"error": "tenant_scope_mismatch"},
                )
            resolved_tenant_id = request_tenant_id
        if not resolved_tenant_id:
            if request is not None:
                raise HTTPException(
                    status_code=401,
                    detail={"error": "tenant_required"},
                )
            raise RuntimeError(
                "A verified tenant is required for normalized orchestration persistence"
            )
        return PostgresOrchestrationRepository(
            tenant_id=resolved_tenant_id,
            report_repository=PostgresReportRepository(
                tenant_id=resolved_tenant_id
            ),
            project_snapshot_repository=PostgresProjectSnapshotRepository(
                tenant_id=resolved_tenant_id
            ),
        )
    return OrchestrationRepository(
        ORCHESTRATIONS_DB,
        report_repository(request, tenant_id=tenant_id),
        market_evidence_repository(),
        project_snapshot_repository(local_compatibility=True),
        persist_state,
        persist_run=persist_orchestration_run,
    )

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def user_id_from_request(request: Request) -> str:
    context = identity_context_from_request(request)
    user = context.get("user") or {}
    return user.get("userId") or "anonymous"


def verified_actor_id(authorization: Dict[str, Any]) -> str:
    """Return only a server-verified identity for an audit mutation."""

    actor_id = str(((authorization.get("user") or {}).get("userId") or "")).strip()
    if authorization.get("authenticated") is not True or not actor_id:
        raise HTTPException(
            status_code=401,
            detail={"error": "authentication_required", "scope": "review_actor"},
        )
    return actor_id

def tenant_id_from_request(request: Request) -> str | None:
    context = identity_context_from_request(request)
    user = context.get("user") or {}
    session = context.get("session") or {}
    return user.get("tenantId") or session.get("tenantId")


def authoritative_project(project_id: str, request: Request) -> Dict[str, Any]:
    project = project_repository(request).get(str(project_id))
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project

def ai_context_from_request(
    request: Request,
    *,
    explicit_provider: str | None = None,
    explicit_user_id: str | None = None,
    payload_ai_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    context = identity_context_from_request(request)
    user = context.get("user") or {}
    session = context.get("session") or {}
    override = dict(payload_ai_context or {})
    inline_secret_fields = sorted(
        key for key in override
        if str(key).lower() in {"apikey", "api_key", "token", "secret", "password"}
    )
    if inline_secret_fields:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "secret_ingest_endpoint_required",
                "message": "AI credential은 /auth/ai-credentials에서 먼저 등록한 뒤 credentialRef로 사용해야 합니다.",
                "fields": inline_secret_fields,
            },
        )
    if explicit_provider:
        override["provider"] = explicit_provider
    user_id = user.get("userId") or explicit_user_id or "anonymous"
    tenant_id = user.get("tenantId") or session.get("tenantId")
    credential_session = credential_session_binding_from_request(request, context)
    return build_user_ai_context(
        user_id=user_id,
        tenant_id=tenant_id,
        session_id=(credential_session or {}).get("sessionId"),
        host="api",
        override=override,
        use_legacy_profile=storage_backend() not in {"postgres", "postgresql"},
    )

def stamp_project_identity(project_data: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """Attach persisted user identity to new project records for user-specific dashboards."""
    context = identity_context_from_request(request)
    if not context.get("authenticated"):
        return project_data

    user = context.get("user") or {}
    session = context.get("session") or {}
    record = dict(project_data)
    metadata = dict(record.get("_meta") or {})
    metadata.setdefault("createdBy", user.get("userId"))
    metadata.setdefault("created_by", user.get("userId"))
    metadata.setdefault("tenantId", user.get("tenantId") or session.get("tenantId"))
    metadata.setdefault("tenant_id", user.get("tenantId") or session.get("tenantId"))
    metadata.setdefault("createdVia", "web")
    metadata["lastUpdatedBy"] = user.get("userId")
    metadata["last_updated_by"] = user.get("userId")
    record["_meta"] = metadata
    return record


_SERVER_MANAGED_PROJECT_AUTHORITY_FIELDS = frozenset(
    {
        "_meta",
        "landCostReview",
        "source_documents",
        "sourceDocuments",
        "canonicalFeasibilityStudyV2",
        "feasibility_input_sources",
        "canonical_feasibility_study_v2",
        "feasibilityStudyV2",
        "feasibility_study_v2",
        "feasibilityStudyV2Draft",
        "feasibility_study_v2_draft",
        "approvedInputSnapshot",
        "approved_input_snapshot",
        "buildableEnvelope",
        "buildable_envelope",
        "regulatoryConstraintSnapshot",
        "regulatory_constraint_snapshot",
        "legalPermitReview",
        "legal_permit_review",
        "legalReview",
        "legal_review",
        "permitReview",
        "permit_review",
    }
)


def reject_server_managed_project_authority_fields(project_data: Dict[str, Any]) -> None:
    """Keep general project CRUD outside server-owned evidence/approval authority."""
    attempted_fields = sorted(
        field for field in project_data if field in _SERVER_MANAGED_PROJECT_AUTHORITY_FIELDS
    )
    if attempted_fields:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "server_managed_project_fields",
                "message": "Server-managed project fields require their dedicated workflow endpoint.",
                "fields": attempted_fields,
            },
        )


def verified_workbook_approval_source_documents(project_id: str) -> list[Dict[str, Any]]:
    """Return only project-scoped archive metadata backed by matching source bytes."""
    archive_repository = upload_archive_repository()
    verified_documents: list[Dict[str, Any]] = []
    for document in list_source_documents_use_case(source_document_repository(), project_id):
        if not isinstance(document, dict):
            continue
        resolved = archive_repository.resolve_download_metadata(project_id, document)
        if not resolved:
            continue
        archive_id = str(resolved.get("archiveId") or "").strip()
        claimed_ids = {
            str(document.get(key) or "").strip()
            for key in ("archiveId", "originalFileHash", "sha256")
            if str(document.get(key) or "").strip()
        }
        if not archive_id or any(claimed_id != archive_id for claimed_id in claimed_ids):
            continue
        verified_documents.append(
            {
                **document,
                "archiveId": archive_id,
                "originalFileHash": archive_id,
                "sha256": archive_id,
                "archiveIntegrityStatus": "verified",
            }
        )
    return verified_documents

def project_visible_to_identity(project: Dict[str, Any], request: Request) -> bool:
    """Keep login dashboards scoped to the authenticated user's stored projects."""
    context = identity_context_from_request(request)
    if not context.get("authenticated"):
        return True

    user = context.get("user") or {}
    user_id = user.get("userId")
    tenant_id = user.get("tenantId")
    metadata = project.get("_meta") if isinstance(project.get("_meta"), dict) else {}
    project_owner = metadata.get("createdBy") or metadata.get("created_by") or metadata.get("uploadedBy")
    project_tenant = metadata.get("tenantId") or metadata.get("tenant_id")
    if project_owner:
        return project_owner == user_id
    if project_tenant:
        return project_tenant == tenant_id
    return False

def record_sensitive_project_event(
    request: Request,
    *,
    event_type: str,
    project_id: str,
    run_id: str | None = None,
    metadata: Dict[str, Any] | None = None,
) -> None:
    event = {
        "eventType": event_type,
        "projectId": project_id,
        "runId": run_id,
        "method": request.method,
        "path": request.url.path,
        "clientHost": request.client.host if request.client else None,
        "userAgent": request.headers.get("user-agent"),
        "requestId": request.headers.get("x-request-id"),
        "metadata": metadata or {},
    }
    if storage_backend() in {"postgres", "postgresql"}:
        context = identity_context_from_request(request)
        user = context.get("user") or {}
        tenant_id = str(user.get("tenantId") or "").strip()
        user_id = str(user.get("userId") or "").strip()
        scope = current_project_scope()
        if context.get("authenticated") is not True or not tenant_id or not user_id or scope is None:
            raise ValueError("Verified project scope, tenant and user are required for audit")
        if scope.tenant_id != tenant_id or scope.get(project_id) is None:
            raise ValueError("Project audit does not match the verified request scope")
        PostgresActivityLogRepository(
            tenant_id=tenant_id, user_id=user_id,
        ).append_project_event(event_type, event)
        return
    record_user_activity_log(
        {**event, "userId": user_id_from_request(request)}
    )

def land_evidence_records_from_ledger(land_ledger: Dict[str, Any]) -> list[Dict[str, Any]]:
    records: list[Dict[str, Any]] = []
    for parcel in land_ledger.get("parcels", []) or []:
        pnu = parcel_pnu(parcel)
        parcel_key = parcel.get("normalizedLotNumber") or parcel.get("addressFragment") or parcel.get("sequence")
        if pnu:
            resolution = parcel.get("pnuResolution") or {}
            enrichment = (parcel.get("enrichmentEvidence") or {}).get("pnuResolution") or {}
            verified_batch = (
                resolution.get("status") == "resolved"
                and resolution.get("lotVerification") == "verified"
                and parcel.get("pnuMatchMethod") in {"pnu_construction", "address_geocode"}
            )
            confidence = parcel.get("pnuResolveConfidence")
            records.append({
                "recordKey": f"pnu_resolution:{pnu}",
                "evidenceId": f"pnu_resolution:{pnu}",
                "evidenceType": "pnu_resolution",
                "pnu": pnu,
                "parcelKey": parcel_key,
                "addressFragment": parcel.get("addressFragment"),
                "matchConfidence": confidence,
                "matchMethod": parcel.get("pnuMatchMethod"),
                "sourceName": enrichment.get("sourceName") or resolution.get("source") or "LandLedgerEnrichmentAgent",
                "sourceType": enrichment.get("sourceType") or ("public_api" if verified_batch else "manual_or_configured_connector"),
                "sourceUrlOrEvidenceId": enrichment.get("sourceUrlOrEvidenceId"),
                "sourceLocator": {"sourceType": "api_row", "apiRowId": pnu} if verified_batch else None,
                "verificationStatus": "resolved" if verified_batch or (
                    isinstance(confidence, (int, float)) and confidence >= 0.85
                ) else "needs_review",
            })
        if parcel.get("cadastralCharacteristics"):
            cadastral = parcel["cadastralCharacteristics"]
            records.append({
                **cadastral,
                "recordKey": f"cadastral_record:{pnu or parcel_key}",
                "evidenceId": f"cadastral_record:{pnu or parcel_key}",
                "evidenceType": "cadastral_record",
                "pnu": pnu,
                "parcelKey": parcel_key,
                "sourceName": cadastral.get("sourceName") or "manual_or_configured_connector",
                "sourceUrlOrEvidenceId": cadastral.get("sourceUrlOrEvidenceId"),
                "verificationStatus": "attached_pending_review",
            })
        if parcel.get("landUsePlanning"):
            land_use = parcel["landUsePlanning"]
            records.append({
                **land_use,
                "recordKey": f"land_use_planning:{pnu or parcel_key}",
                "evidenceId": f"land_use_planning:{pnu or parcel_key}",
                "evidenceType": "land_use_planning",
                "pnu": pnu,
                "parcelKey": parcel_key,
                "sourceName": land_use.get("sourceName") or "configured_land_use_planning_connector",
                "sourceUrlOrEvidenceId": land_use.get("sourceUrlOrEvidenceId"),
                "verificationStatus": "attached_pending_review",
            })
        for building in parcel.get("buildingUseRecords", []) or []:
            records.append({
                **building,
                "recordKey": f"building_use:{building.get('buildingId') or building.get('gisBuildingId') or pnu or parcel_key}",
                "evidenceId": f"building_use:{building.get('buildingId') or building.get('gisBuildingId') or pnu or parcel_key}",
                "evidenceType": "building_use",
                "pnu": pnu,
                "parcelKey": parcel_key,
                "sourceName": building.get("sourceName") or "VWorld building use attributes",
                "sourceUrlOrEvidenceId": building.get("sourceUrlOrEvidenceId"),
                "verificationStatus": "attached_pending_review",
            })
        if parcel.get("landCharacteristics"):
            characteristics = parcel["landCharacteristics"]
            records.append({
                **characteristics,
                "recordKey": f"land_characteristics:{pnu or parcel_key}",
                "evidenceId": f"land_characteristics:{pnu or parcel_key}",
                "evidenceType": "land_characteristics",
                "pnu": pnu,
                "parcelKey": parcel_key,
                "sourceName": characteristics.get("sourceName") or "VWorld land characteristics",
                "sourceUrlOrEvidenceId": characteristics.get("sourceUrlOrEvidenceId"),
                "verificationStatus": "attached_pending_review",
            })
        if parcel.get("officialLandPricePerSqm"):
            records.append({
                "recordKey": f"official_land_price:{pnu or parcel_key}",
                "evidenceId": parcel.get("officialLandPriceSourceUrlOrEvidenceId") or f"official_land_price:{pnu or parcel_key}",
                "evidenceType": "official_land_price",
                "pnu": pnu,
                "parcelKey": parcel_key,
                "pricePerSqm": parcel.get("officialLandPricePerSqm"),
                "officialTotalLandValue": parcel.get("officialTotalLandValue"),
                "baseYear": parcel.get("officialLandPriceBaseYear"),
                "baseDate": parcel.get("officialLandPriceBaseDate"),
                "sourceName": parcel.get("officialLandPriceSource"),
                "sourceUrlOrEvidenceId": parcel.get("officialLandPriceSourceUrlOrEvidenceId"),
                "fetchedAt": parcel.get("officialLandPriceFetchedAt"),
                "verificationStatus": "attached_pending_review",
            })
        if parcel.get("registryEvidence"):
            registry = parcel["registryEvidence"]
            records.append({
                **registry,
                "recordKey": f"registry_record:{pnu or parcel_key}",
                "evidenceId": registry.get("evidenceId") or f"registry_record:{pnu or parcel_key}",
                "evidenceType": "registry_record",
                "pnu": pnu,
                "parcelKey": parcel_key,
                "sourceName": registry.get("source") or "manual_registry_evidence",
                "sourceUrlOrEvidenceId": registry.get("sourceUrlOrEvidenceId") or registry.get("evidenceId"),
                "verificationStatus": registry.get("verificationStatus", "attached_pending_legal_review"),
            })
    return records

def land_ledger_auto_enrichment_options(project_id: str, land_ledger: Dict[str, Any], options: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Add source-backed matches for individual ledger parcel addresses."""
    merged = dict(options or {})
    project = _project_store().get(project_id) or {}

    if "autoResolvePnuFromAddress" not in merged and "auto_resolve_pnu_from_address" not in merged:
        merged["autoResolvePnuFromAddress"] = True

    if not (merged.get("autoResolvePnuFromAddress") or merged.get("auto_resolve_pnu_from_address")):
        return merged

    project_base_address = _legal_dong_base_address((project.get("land") or {}).get("address"))
    parcels = land_ledger.get("parcels") or []
    max_parcels = int(merged.get("autoResolvePnuLimit") or merged.get("auto_resolve_pnu_limit") or 150)
    pnu_resolutions = list(merged.get("pnuResolutions") or merged.get("pnu_resolutions") or [])
    official_land_prices = list(merged.get("officialLandPrices") or merged.get("official_land_prices") or [])
    cadastral_records = list(merged.get("cadastralCharacteristics") or merged.get("cadastral_characteristics") or [])
    diagnostics: list[Dict[str, Any]] = []
    seen_keys = {
        str(record.get("pnu") or record.get("resolvedPnu") or record.get("parcelKey") or "")
        for record in pnu_resolutions
        if isinstance(record, dict)
    }

    for parcel in parcels[:max_parcels]:
        if parcel_pnu(parcel):
            continue
        address = parcel_lookup_address(parcel, project_base_address or "")
        if not address:
            diagnostics.append({
                "connector": "vworld_address_pnu",
                "status": "skipped_missing_address",
                "sequence": parcel.get("sequence"),
            })
            continue

        resolved = resolve_address_to_parcel(address)
        mismatch = parcel_address_lookup_mismatch(parcel, address, resolved)
        if not mismatch:
            key = str(resolved["pnu"])
            parcel_key = parcel.get("sequence") or parcel.get("addressFragment") or parcel.get("normalizedLotNumber")
            if key not in seen_keys:
                pnu_resolutions.append({
                    "parcelKey": parcel_key,
                    "sequence": parcel.get("sequence"),
                    "addressFragment": parcel.get("addressFragment") or address,
                    "pnu": resolved["pnu"],
                    "resolvedPnu": resolved["pnu"],
                    "resolvedLegalAddress": resolved.get("addressFragment") or address,
                    "matchConfidence": 0.92 if project_base_address else 0.85,
                    "matchMethod": "vworld_address_to_parcel_lookup",
                    "candidateCount": 1,
                    "sourceName": "VWorld parcel lookup",
                    "sourceType": "public_api",
                    "sourceUrlOrEvidenceId": "vworld:address-to-parcel",
                    "baseDate": None,
                })
                seen_keys.add(key)
            if resolved.get("officialLandPricePerSqm"):
                official_land_prices.append({
                    "pnu": resolved["pnu"],
                    "parcelKey": parcel_key,
                    "pricePerSqm": resolved.get("officialLandPricePerSqm"),
                    "baseYear": resolved.get("officialLandPriceBaseYear"),
                    "baseDate": resolved.get("officialLandPriceBaseDate"),
                    "sourceName": "VWorld cadastral jiga",
                    "sourceType": "public_api",
                    "sourceUrlOrEvidenceId": "vworld:LP_PA_CBND_BUBUN:jiga",
                    "confidence": 0.95,
                    "fetchedAt": now_iso(),
                })
            cadastral_records.append({
                "pnu": resolved["pnu"],
                "parcelKey": parcel_key,
                "landCategory": resolved.get("landCategory"),
                "areaSqm": resolved.get("areaSqm"),
                "sourceName": "VWorld cadastral parcel",
                "sourceType": "public_api",
                "sourceUrlOrEvidenceId": "vworld:LP_PA_CBND_BUBUN",
                "fetchedAt": now_iso(),
            })
            diagnostics.append({
                "connector": "vworld_address_pnu",
                "status": "resolved",
                "sequence": parcel.get("sequence"),
                "addressFragment": address,
                "pnu": resolved["pnu"],
            })
        else:
            diagnostics.append({
                "connector": "vworld_address_pnu",
                "status": "unresolved",
                "reason": mismatch,
                "sequence": parcel.get("sequence"),
                "addressFragment": address,
            })

    if pnu_resolutions:
        merged["pnuResolutions"] = pnu_resolutions
    if official_land_prices:
        merged["officialLandPrices"] = official_land_prices
    if cadastral_records:
        merged["cadastralCharacteristics"] = cadastral_records
    if diagnostics:
        merged["autoPnuDiagnostics"] = diagnostics
    return merged


def _legal_dong_base_address(address: Any) -> str | None:
    text = str(address or "").strip()
    if not text:
        return None
    text = re.sub(r"\s+", " ", text)
    text = text.replace("일원", "").replace("일대", "")
    text = re.sub(r"\d+\s*(?:-\s*\d+)?\s*번지?.*$", "", text).strip()
    text = re.sub(r"(?<=[가-힣])(도|특별시|광역시|특별자치시|특별자치도|시|군|구|읍|면|동|리|가)(?=[가-힣])", r"\1 ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def persisted_land_evidence_package(project_id: str) -> Dict[str, Any]:
    records = list_land_evidence_records(project_id)
    by_type: Dict[str, list[Dict[str, Any]]] = {}
    for record in records:
        by_type.setdefault(record.get("evidenceType") or "land_evidence", []).append(record)

    first_official_price = next(
        (
            record
            for record in records
            if record.get("evidenceType") == "official_land_price"
        ),
        None,
    )
    registry_records = by_type.get("registry_record", [])
    pnu_records = by_type.get("pnu_resolution", [])
    acquisition_records = by_type.get("land_acquisition_status", [])
    latest_acquisition = acquisition_records[0] if acquisition_records else None
    acquisition_ratio = None
    if latest_acquisition:
        acquisition_ratio = (
            latest_acquisition.get("acquisitionAreaRatio")
            or latest_acquisition.get("acquisition_area_ratio")
            or latest_acquisition.get("acquisitionRatio")
        )
    return {
        "schemaVersion": "realytics-land-evidence-package-v1",
        "recordCount": len(records),
        "records": records,
        "recordsByType": by_type,
        "pnuResolutions": pnu_records,
        "cadastralCharacteristics": by_type.get("cadastral_record", []),
        "landUsePlanningRecords": by_type.get("land_use_planning", []),
        "buildingUseRecords": by_type.get("building_use", []),
        "landCharacteristicsRecords": by_type.get("land_characteristics", []),
        "regionLandPriceTrends": by_type.get("land_price_trend", []),
        "officialLandPrices": by_type.get("official_land_price", []),
        "registryEvidence": registry_records,
        "parcelCount": len({record.get("pnu") or record.get("parcelKey") for record in records if record.get("pnu") or record.get("parcelKey")}),
        "officialLandPrice": first_official_price.get("pricePerSqm") if first_official_price else None,
        "officialLandPriceBaseYear": first_official_price.get("baseYear") if first_official_price else None,
        "officialLandPriceEvidence": first_official_price,
        "registryEvidenceReady": bool(registry_records),
        "landAcquisitionEvidence": latest_acquisition,
        "acquisitionAreaRatio": acquisition_ratio,
        "acquisitionEvidenceReady": bool(latest_acquisition),
        "sourceMetadata": {
            "sourceName": "server_land_evidence_store",
            "sourceType": "runtime_db",
            "recordCount": len(records),
        },
    }

def attach_persisted_land_evidence(project_id: str, project_data: Dict[str, Any]) -> Dict[str, Any]:
    package = persisted_land_evidence_package(project_id)
    if not package["records"]:
        return project_data

    enriched_project = dict(project_data)
    existing_evidence = enriched_project.get("landEvidence") or enriched_project.get("land_evidence") or {}
    enriched_project["landEvidence"] = deep_merge(existing_evidence, package)

    land_ledger = enriched_project.get("landLedger") or enriched_project.get("land_ledger")
    if isinstance(land_ledger, dict) and land_ledger.get("parcels"):
        try:
            enriched_project["landLedger"] = enrich_land_ledger_use_case(
                land_ledger,
                {
                    "pnuResolutions": package.get("pnuResolutions", []),
                    "cadastralCharacteristics": package.get("cadastralCharacteristics", []),
                    "landUsePlanningRecords": package.get("landUsePlanningRecords", []),
                    "buildingUseRecords": package.get("buildingUseRecords", []),
                    "landCharacteristicsRecords": package.get("landCharacteristicsRecords", []),
                    "regionLandPriceTrends": package.get("regionLandPriceTrends", []),
                    "officialLandPrices": package.get("officialLandPrices", []),
                    "registryEvidence": package.get("registryEvidence", []),
                    "mode": "server_land_evidence_store",
                },
            )
        except Exception as exc:
            enriched_project["landEvidence"]["ledgerHydrationWarning"] = {
                "message": "Stored land evidence was attached as a flat package, but could not be merged into the land ledger.",
                "error": "land_ledger_hydration_failed",
            }
    return enriched_project


def persisted_parsed_fact_package(project_id: str) -> Dict[str, Any]:
    return parsed_fact_package(project_id)


def attach_persisted_parsed_facts(project_id: str, project_data: Dict[str, Any]) -> Dict[str, Any]:
    package = persisted_parsed_fact_package(project_id)
    if not package.get("recordCount"):
        return project_data
    enriched_project = dict(project_data)
    enriched_project["parsedFacts"] = package.get("records") or []
    enriched_project["parsedFactsSummary"] = package.get("summary") or {}
    enriched_project["parsedFactPackage"] = {
        key: value
        for key, value in package.items()
        if key != "records"
    }
    return enriched_project


def attach_approved_assumption_overrides(project_id: str, project_data: Dict[str, Any]) -> Dict[str, Any]:
    if storage_backend() in {"postgres", "postgresql"}:
        from use_cases.approval_readiness_gate import load_active_normalized_assumption_overrides

        scope = current_project_scope()
        if scope is None:
            raise RuntimeError("A verified project scope is required for assumption overrides")
        overrides = load_active_normalized_assumption_overrides(project_id, tenant_id=scope.tenant_id)
        enriched_project = dict(project_data)
        enriched_project.pop("approved_assumption_overrides", None)
        enriched_project["approvedAssumptionOverrides"] = overrides
        metadata = project_data.get("sourceMetadata")
        enriched_project["sourceMetadata"] = {
            **(metadata if isinstance(metadata, dict) else {}),
            "approvedAssumptionOverrideCount": len(overrides),
        }
        return enriched_project

    overrides = list_assumption_override_records(project_id, status="active")
    if not overrides:
        return project_data
    enriched_project = dict(project_data)
    existing = enriched_project.get("approvedAssumptionOverrides") or enriched_project.get("approved_assumption_overrides") or []
    if not isinstance(existing, list):
        existing = []
    by_id = {
        str(item.get("overrideId") or item.get("override_id")): item
        for item in existing
        if isinstance(item, dict) and (item.get("overrideId") or item.get("override_id"))
    }
    for override in overrides:
        by_id[override["overrideId"]] = override
    enriched_project["approvedAssumptionOverrides"] = list(by_id.values())
    enriched_project.setdefault("sourceMetadata", {})
    if isinstance(enriched_project["sourceMetadata"], dict):
        enriched_project["sourceMetadata"]["approvedAssumptionOverrideCount"] = len(overrides)
    return enriched_project


def _metric_rows_from_result(result: Dict[str, Any], *, prefix: str = "") -> list[Dict[str, Any]]:
    rows: list[Dict[str, Any]] = []
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    for key, metric in metrics.items():
        if isinstance(metric, dict):
            row = {
                "key": f"{prefix}{key}",
                "value": metric.get("value"),
                "unit": metric.get("unit"),
                "label": metric.get("label") or key,
                "status": metric.get("status"),
            }
            if metric.get("sourceLocator") or metric.get("source_locator"):
                row["sourceLocator"] = metric.get("sourceLocator") or metric.get("source_locator")
            rows.append(row)
        else:
            rows.append({"key": f"{prefix}{key}", "value": metric})
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    for key, value in summary.items():
        if isinstance(value, (int, float)):
            rows.append({"key": f"{prefix}summary.{key}", "value": value})
    for key, value in result.items():
        if key in {"metrics", "summary", "riskFlags", "risk_flags", "riskIssues", "monthlyCashFlow", "cashflows"}:
            continue
        if isinstance(value, (int, float)):
            rows.append({"key": f"{prefix}{key}", "value": value})
    section_fields = {
        "revenue": {"totalRevenue", "totalSalesRevenue"},
        "costs": {"totalCost", "totalProjectCost"},
        "developerReturns": {"developmentProfit", "profitMargin", "profitOnCost", "breakEvenSaleRate"},
        "pfReadiness": {"ltvByRevenue", "ltcByCost", "dscr", "icr", "exitSaleRate", "recommendedPfLimit"},
        "architecturalProgram": {"landAreaSqm", "grossFloorAreaSqm", "saleableAreaSqm", "unitCount"},
        "summary": {"maxMinus", "peakLoanBalance", "totalRevenue", "totalCost", "developmentProfit"},
    }
    for section, fields in section_fields.items():
        values = result.get(section)
        if not isinstance(values, dict):
            continue
        for key in fields:
            value = values.get(key)
            if isinstance(value, (int, float)):
                rows.append({"key": f"{prefix}{section}.{key}", "value": value})
    return rows


def _risk_rows_from_result(result: Dict[str, Any]) -> list[Dict[str, Any]]:
    risks = result.get("riskFlags") or result.get("risk_flags") or result.get("riskIssues") or []
    return [risk for risk in risks if isinstance(risk, dict)]


def _monthly_cashflow_metric_rows(result: Dict[str, Any], engine_key: str) -> list[Dict[str, Any]]:
    """Index monthly rows in the deal-state ledger without replacing the run snapshot."""
    rows: list[Dict[str, Any]] = []
    monthly = result.get("monthlyCashFlow") or result.get("monthly_cash_flow") or result.get("rows") or []
    if not isinstance(monthly, list):
        return rows
    for index, item in enumerate(monthly):
        if not isinstance(item, dict):
            continue
        month = item.get("monthIndex", item.get("month_index", index))
        period = item.get("period") or f"M{month}"
        for field in ("netCashFlow", "net_cash_flow", "net_cashflow", "cumulativeCashFlowBeforePfDraw", "cumulativeCashFlow", "cumulative_cash_flow", "cumulative_cashflows", "pfDrawRequired", "totalPfDraw"):
            value = item.get(field)
            if isinstance(value, (int, float)):
                canonical = {
                    "netCashFlow": "netCashFlow",
                    "net_cash_flow": "netCashFlow",
                    "net_cashflow": "netCashFlow",
                    "cumulativeCashFlowBeforePfDraw": "cumulativeCashFlow",
                    "cumulativeCashFlow": "cumulativeCashFlow",
                    "cumulative_cash_flow": "cumulativeCashFlow",
                    "cumulative_cashflows": "cumulativeCashFlow",
                    "pfDrawRequired": "pfDrawRequired",
                    "totalPfDraw": "pfDraw",
                }[field]
                rows.append({
                    "key": f"{engine_key}.monthly.{period}.{canonical}",
                    "value": value,
                    "unit": "KRW",
                    "sourceLocator": item.get("sourceLocator") or item.get("source_locator") or {
                        "sourceType": "calculation_run_monthly_row",
                        "engineKey": engine_key,
                        "monthIndex": month,
                        "period": period,
                    },
                })
    return rows


def _persist_calculation_run(
    project_id: str,
    *,
    engine_key: str,
    result: Dict[str, Any],
    input_data: Dict[str, Any] | None = None,
    artifact_ref: Dict[str, Any] | None = None,
    effective_input_captured: bool = False,
) -> Dict[str, Any]:
    input_data = input_data or {}
    source_locator = (
        input_data.get("sourceLocator")
        or input_data.get("source_locator")
        or artifact_ref
        or {"engineKey": engine_key}
    )
    scenario = input_data.get("scenario") or input_data.get("scenarioContext") or input_data.get("scenario_context")
    if not scenario and input_data.get("scenarioId"):
        scenario = {"scenarioId": input_data.get("scenarioId")}
    return upsert_calculation_run_record(
        project_id,
        {
            "engineKey": engine_key,
            "financialInputHash": input_data.get("financialInputHash"),
            "engineVersion": (result.get("engineVersion") if engine_key == "cash_flow_engine" else None)
            or input_data.get("engineVersion") or f"{engine_key}-v1",
            "inputEvidenceIds": input_data.get("inputEvidenceIds")
            or input_data.get("evidenceIds")
            or ([input_data.get("sourceDocumentId")] if input_data.get("sourceDocumentId") else []),
            "assumptionIds": input_data.get("assumptionIds") or [],
            "outputMetrics": _metric_rows_from_result(result, prefix=f"{engine_key}." ) + _monthly_cashflow_metric_rows(result, engine_key),
            "riskIssues": _risk_rows_from_result(result),
            "inputSnapshot": input_data,
            "resultSnapshot": result,
            "scenario": scenario or {},
            "artifactRef": artifact_ref or {},
            "sourceLocator": source_locator,
            "inputDigest": {
                **({"capturePolicy": "effective_request_v1"} if effective_input_captured else {}),
                "keys": sorted(str(key) for key in input_data.keys()),
                "source": input_data.get("source") or input_data.get("sourceName") or "api_request",
                "sourceLocator": source_locator,
            },
        },
    )

def _persist_scenario_calculation_runs(
    project_id: str,
    scenario_dump: Dict[str, Any],
    input_data: Dict[str, Any] | None = None,
    *,
    engines: list[str] | None = None,
    strict: bool = True,
) -> Dict[str, str]:
    """Persist Waterfall / Unsold LTV / Tranche Recovery runs from one scenario
    set. Returns {engineKey: calculationRunId}. Persistence failures propagate
    so callers can expose an incomplete dependent execution."""
    run_ids: Dict[str, str] = {}
    try:
        payloads = scenario_engine_run_payloads(scenario_dump, engines=engines)
    except Exception:
        if strict:
            raise
        return run_ids
    for payload in payloads:
        try:
            run = _persist_calculation_run(
                project_id,
                engine_key=payload["engineKey"],
                result=payload["result"],
                input_data={**(input_data or {}), "scenarioSetId": scenario_dump["scenarioSetId"],
                            "engineVersion": payload["engineVersion"]},
                artifact_ref=payload["artifactRef"],
                effective_input_captured=True,
            )
            run_ids[payload["engineKey"]] = str(run.get("calculationRunId"))
        except Exception:
            if strict:
                raise
            continue
    return run_ids


def attach_active_restructuring_cures(project_id: str, project_data: Dict[str, Any]) -> Dict[str, Any]:
    if storage_backend() in {"postgres", "postgresql"}:
        # The normalized project already carries any saved cure projection.
        return project_data
    cures = list_restructuring_cure_records(project_id, status="active")
    if not cures:
        return project_data
    enriched_project = dict(project_data)
    existing = enriched_project.get("adoptedRestructuringCures") or enriched_project.get("adopted_restructuring_cures") or []
    if not isinstance(existing, list):
        existing = []
    by_id = {
        str(item.get("cureId") or item.get("cure_id")): item
        for item in existing
        if isinstance(item, dict) and (item.get("cureId") or item.get("cure_id"))
    }
    for cure in cures:
        by_id[cure["cureId"]] = cure
    enriched_project["adoptedRestructuringCures"] = list(by_id.values())
    enriched_project.setdefault("sourceMetadata", {})
    if isinstance(enriched_project["sourceMetadata"], dict):
        enriched_project["sourceMetadata"]["adoptedRestructuringCureCount"] = len(cures)
    return enriched_project


def persist_work_order_prompt_artifact(
    project_id: str,
    plan: Dict[str, Any],
    *,
    created_by: str,
    source: str = "orchestration_plan",
) -> Dict[str, Any] | None:
    prompt_draft = plan.get("promptDraft") if isinstance(plan, dict) else None
    if not isinstance(prompt_draft, dict) or not prompt_draft.get("body"):
        return None
    artifact = upsert_prompt_artifact_record(
        project_id,
        {
            "artifactType": "ai_work_order",
            "workflow": plan.get("recommendedWorkflow"),
            "title": prompt_draft.get("title") or "AI 업무 주문서",
            "body": prompt_draft.get("body"),
            "intendedTool": prompt_draft.get("intendedTool"),
            "createdBy": created_by,
            "source": source,
            "recommendedWorkflow": plan.get("recommendedWorkflow"),
            "outputOptions": plan.get("outputOptions") or [],
            "validationOptions": plan.get("validationOptions") or [],
            "dataAvailability": plan.get("dataAvailability") or {},
            "summary": plan.get("summary"),
            "evidenceGraphHints": {
                "nodeType": "prompt_artifact",
                "connectsTo": ["orchestration_plan", "selected_workflow", "validation_conditions"],
            },
        },
    )
    return artifact


def attach_document_market_evidence_metadata(
    parsed_data: Dict[str, Any],
    upload_meta: Dict[str, Any],
) -> None:
    document_market_evidence = parsed_data.get("document_market_evidence")
    if not isinstance(document_market_evidence, dict):
        document_market_evidence = {}
        parsed_data["document_market_evidence"] = document_market_evidence

    document_market_evidence.setdefault("sourceDocument", upload_meta)
    document_market_evidence.setdefault("parserVersion", PARSER_AGENT_VERSION)

    sales_assumptions = parsed_data.get("sales_assumptions", {})
    product_mix = (
        document_market_evidence.get("productMix")
        or document_market_evidence.get("product_mix")
        or parsed_data.get("product_mix")
        or parsed_data.get("productMix")
        or sales_assumptions.get("product_mix")
        or sales_assumptions.get("productMix")
    )
    if product_mix and not document_market_evidence.get("productMix"):
        document_market_evidence["productMix"] = product_mix

    market = parsed_data.get("market", {})
    if isinstance(market, dict):
        for key in [
            "comparables",
            "manualComparables",
            "manual_comparables",
            "transactionComparables",
            "transaction_comparables",
            "presaleComparables",
            "presale_comparables",
            "listingComparables",
            "listing_comparables",
            "rentComparables",
            "rent_comparables",
            "rentalAssumptions",
            "rental_assumptions",
            "data_sources",
            "dataSources",
        ]:
            if market.get(key) and not document_market_evidence.get(key):
                document_market_evidence[key] = market[key]

        for key in ["proposedPricePerPy", "demandLevel", "upcomingSupplyUnits"]:
            if market.get(key) and not document_market_evidence.get(key):
                document_market_evidence[key] = market[key]

async def parse_uploaded_business_plan(
    project_id: str,
    file: UploadFile,
    ai_context: Dict[str, Any],
) -> Dict[str, Any]:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name
    uploaded_at = now_iso()
    archive_meta = archive_source_document_use_case(
        upload_archive_repository(),
        project_id,
        content,
        file.filename,
        file.content_type,
        tenant_id=_project_tenant_id(project_id),
    )
    upload_meta = {
        "originalFileName": file.filename,
        "originalFileHash": archive_meta["archiveId"],
        "documentType": "business_plan",
        "contentType": file.content_type,
        "fileSizeBytes": len(content),
        "uploadedAt": uploaded_at,
        "parserVersion": PARSER_AGENT_VERSION,
        **archive_meta,
    }

    try:
        parser_ai_context = {
            **ai_context,
            "project_id": project_id,
            "agent_id": "ParserAgent",
        }
        agent = ParserAgent(parser_ai_context)
        text = agent.parse_document(tmp_path)
        parsed_data = agent.extract_project_data(text, parser_ai_context)
        parsed_data.setdefault("_meta", {})
        parsed_data["_meta"]["upload"] = upload_meta
        parsed_data["_meta"]["parser_version"] = PARSER_AGENT_VERSION
        parsed_data.setdefault("source_documents", [])
        parsed_data["source_documents"].append(upload_meta)

        attach_document_market_evidence_metadata(parsed_data, upload_meta)
        attach_parser_field_provenance(
            parsed_data,
            upload_meta=upload_meta,
            parser_version=PARSER_AGENT_VERSION,
        )
        parsed_fact_result = persist_parsed_facts(project_id, parsed_data)
        parsed_data["_meta"]["parsedFactStore"] = {
            key: value
            for key, value in parsed_fact_result.items()
            if key != "records"
        }
        parsed_data["parsedFactsSummary"] = parsed_fact_result.get("summary") or {}

        return parsed_data
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

async def parse_uploaded_land_ledger(project_id: str, file: UploadFile) -> Dict[str, Any]:
    suffix = os.path.splitext(file.filename or "")[1] or ".xls"
    uploaded_at = now_iso()
    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "empty_file",
                "message": "빈 파일입니다. 내용이 있는 토지조서 파일을 올려주세요.",
                "originalFileName": file.filename,
            },
        )
    _require_workbook_format("land_ledger", file.filename, content[:1024])
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    archive_meta = archive_source_document_use_case(
        upload_archive_repository(),
        project_id,
        content,
        file.filename,
        file.content_type,
        tenant_id=_project_tenant_id(project_id),
    )
    upload_meta = {
        "originalFileName": file.filename,
        "originalFileHash": archive_meta["archiveId"],
        "documentType": "land_ledger",
        "contentType": file.content_type,
        "fileSizeBytes": len(content),
        "uploadedAt": uploaded_at,
        "parserVersion": LAND_LEDGER_AGENT_VERSION,
        **archive_meta,
    }

    try:
        return analyze_land_ledger_workbook_use_case(
            tmp_path,
            {
                "import_id": str(uuid4()),
                "original_file_name": file.filename,
                "original_file_hash": archive_meta["archiveId"],
                "uploaded_at": uploaded_at,
            },
        ) | {
            "upload": upload_meta
        }
    except LandLedgerParseError as exc:
        # Unreadable workbook (empty/unsupported/corrupt): graceful 422 instead of a 500.
        raise HTTPException(
            status_code=422,
            detail={
                "error": "land_ledger_parse_failed",
                "message": str(exc),
                "originalFileName": file.filename,
            },
        ) from exc
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

async def validate_uploaded_feasibility_workbook(project_id: str, file: UploadFile) -> Dict[str, Any]:
    suffix = os.path.splitext(file.filename or "")[1] or ".xlsx"
    uploaded_at = now_iso()
    content = await file.read()
    _require_workbook_format("feasibility_workbook", file.filename, content[:1024])
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    archive_meta = archive_source_document_use_case(
        upload_archive_repository(),
        project_id,
        content,
        file.filename,
        file.content_type,
        tenant_id=_project_tenant_id(project_id),
    )
    upload_meta = {
        "originalFileName": file.filename,
        "originalFileHash": archive_meta["archiveId"],
        "documentType": "feasibility_workbook",
        "contentType": file.content_type,
        "fileSizeBytes": len(content),
        "uploadedAt": uploaded_at,
        "parserVersion": "feasibility-workbook-validator-v1",
        **archive_meta,
    }

    try:
        feasibility_study_v2_draft = None
        try:
            validation = validate_feasibility_workbook(tmp_path)
            validation["sourceDocumentHash"] = upload_meta["originalFileHash"]
            upload_meta["validationStatus"] = validation.get("status")
            upload_meta["validation"] = validation
            feasibility_study_v2_draft = import_feasibility_study_from_workbook(
                tmp_path,
                project_id=project_id,
                source_document=upload_meta,
            )
            upload_meta["feasibilityStudyV2DraftHash"] = feasibility_study_draft_hash(
                feasibility_study_v2_draft
            )
        except Exception as exc:
            validation = {
                "status": "parse_failed",
                "fileName": file.filename,
                "error": str(exc),
                "recommendations": [
                    "Upload an .xlsx workbook compatible with the realytics-fs-xlsx-v1 contract or legacy Bonri workbook profile."
                ],
            }
        validation["sourceDocumentHash"] = upload_meta["originalFileHash"]
        upload_meta["validationStatus"] = validation.get("status")
        upload_meta["validation"] = validation
        return {
            "project_id": project_id,
            "upload": upload_meta,
            "validation": validation,
            "feasibilityStudyV2Draft": feasibility_study_v2_draft,
            "note": "Workbook validation is read-only; a FeasibilityStudyV2 draft is returned for reviewer comparison before persisted import.",
        }
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

async def validate_uploaded_risk_report_workbook(project_id: str, file: UploadFile) -> Dict[str, Any]:
    suffix = os.path.splitext(file.filename or "")[1] or ".xlsx"
    uploaded_at = now_iso()
    content = await file.read()
    _require_workbook_format("risk_report_workbook", file.filename, content[:1024])
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    archive_meta = archive_source_document_use_case(
        upload_archive_repository(),
        project_id,
        content,
        file.filename,
        file.content_type,
        tenant_id=_project_tenant_id(project_id),
    )
    upload_meta = {
        "originalFileName": file.filename,
        "originalFileHash": archive_meta["archiveId"],
        "documentType": "risk_report_workbook",
        "contentType": file.content_type,
        "fileSizeBytes": len(content),
        "uploadedAt": uploaded_at,
        "parserVersion": "risk-report-workbook-importer-v1",
        **archive_meta,
    }

    try:
        try:
            draft = import_risk_report_workbook(
                tmp_path,
                project_id=project_id,
                source_document=upload_meta,
            )
        except Exception as exc:
            draft = {
                "schemaVersion": "realytics-risk-report-xlsx-v1",
                "analysisType": "risk_report_workbook_import_draft",
                "validation": {
                    "status": "parse_failed",
                    "importReady": False,
                    "error": str(exc),
                    "recommendations": [
                        "Upload a readable .xlsx file generated from the risk-report-xlsx-v1 contract."
                    ],
                },
                "sourceDocument": upload_meta,
            }
        upload_meta["validationStatus"] = (draft.get("validation") or {}).get("status")
        return {
            "project_id": project_id,
            "upload": upload_meta,
            "riskReportImportDraft": draft,
            "note": "Risk report workbook validation is read-only. Use the approve endpoint to persist Risk Register rows and report sections.",
        }
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

async def learn_uploaded_feasibility_workbook(
    project_id: str,
    file: UploadFile,
    *,
    tenant_id: str = "default",
    created_by: str = "system",
) -> Dict[str, Any]:
    suffix = os.path.splitext(file.filename or "")[1] or ".xlsx"
    uploaded_at = now_iso()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    archive_meta = archive_source_document_use_case(
        upload_archive_repository(),
        project_id,
        content,
        file.filename,
        file.content_type,
        tenant_id=_project_tenant_id(project_id),
    )
    upload_meta = {
        "originalFileName": file.filename,
        "originalFileHash": archive_meta["archiveId"],
        "documentType": "feasibility_workbook_learning_candidate",
        "contentType": file.content_type,
        "fileSizeBytes": len(content),
        "uploadedAt": uploaded_at,
        "parserVersion": "excel-workbook-learning-agent-v1",
        **archive_meta,
    }

    try:
        try:
            learning = learn_excel_workbook_mapping_use_case(tmp_path, source_document=upload_meta)
        except Exception as exc:
            learning = {
                "analysisType": "realytics-excel-workbook-learning-v1",
                "status": "profile_failed",
                "fileName": file.filename,
                "error": str(exc),
                "warnings": [
                    "Workbook could not be profiled. Keep the original archived file and request manual review."
                ],
                "nextActions": [
                    "Confirm the file is a readable .xlsx workbook.",
                    "If the file is .xls, convert or route it through the legacy workbook adapter.",
                ],
            }
        upload_meta["learningStatus"] = learning.get("status")
        lifecycle_candidate = None
        mapping_candidates = learning.get("mappingCandidates") or []
        if mapping_candidates:
            now = datetime.now(timezone.utc)
            selected_template = (learning.get("templateCandidates") or [{}])[0] or {}
            target_key = str(
                selected_template.get("templateId")
                or upload_meta.get("archiveId")
                or upload_meta.get("originalFileHash")
            )
            candidate = LearningCandidate(
                candidate_id=f"learning-candidate:{uuid4()}",
                tenant_id=tenant_id,
                project_id=project_id,
                candidate_type="feasibility_workbook_mapping",
                target_key=target_key,
                proposed_change={
                    "selectedTemplate": selected_template,
                    "mappingCandidates": mapping_candidates,
                    "validationPlan": learning.get("validationPlan") or {},
                    "automationDecision": learning.get("automationDecision") or {},
                },
                evidence_ids=[str(upload_meta.get("archiveId") or upload_meta.get("originalFileHash"))],
                created_by=created_by,
                created_at=now,
                updated_at=now,
                metadata={
                    "sourceDocumentType": upload_meta.get("documentType"),
                    "parserVersion": upload_meta.get("parserVersion"),
                    "automaticPromotionAllowed": False,
                },
            )
            lifecycle_candidate = LearningLifecycleService(
                OperationalRepository(),
            ).create_candidate(candidate).model_dump(by_alias=True, mode="json")
        return {
            "project_id": project_id,
            "upload": upload_meta,
            "learning": learning,
            "learningCandidate": lifecycle_candidate,
            "note": (
                "Workbook learning is read-only. Mapping candidates require LLM judgement, "
                "automated validation, shadow-mode import, and regression tests before they "
                "become monitored production import rules."
            ),
        }
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

def load_fixtures():
    global project_id_counter, REPORTS_DB, ORCHESTRATIONS_DB, MARKET_EVIDENCE_DB, PROJECT_SNAPSHOTS_DB
    if storage_backend() in {"postgres", "postgresql"}:
        return
    state = load_state(project_workspace_only=True)
    if state:
        _project_store().update(state["projects"])
        REPORTS_DB = state["reports"]
        ORCHESTRATIONS_DB = state["orchestrations"]
        MARKET_EVIDENCE_DB = state.get("market_evidence", {})
        PROJECT_SNAPSHOTS_DB = state.get("project_snapshots", {})
        project_id_counter = state["project_id_counter"]
        return

    examples_dir = os.path.join(os.path.dirname(__file__), "..", "data", "examples")
    if os.path.exists(examples_dir):
        for filename in os.listdir(examples_dir):
            if filename.endswith(".json"):
                with open(os.path.join(examples_dir, filename), "r", encoding="utf-8") as f:
                    data = json.load(f)
                    pid = str(project_id_counter)
                    data["project"]["id"] = pid
                    _project_store()[pid] = data
                    project_id_counter += 1
        persist_state()

load_fixtures()

@router.get("")
@router.get("/", include_in_schema=False)
def get_projects(request: Request):
    authorize_request(request, "project:read")
    projects = list_projects_use_case(project_repository(request))
    return [project for project in projects if project_visible_to_identity(project, request)]


@router.get("/recent-work")
def get_recent_work(
    request: Request,
    response: Response,
    limit: int = Query(5, ge=1, le=20),
):
    """Return the signed-in user's resumable deal-room work, not demo cards."""
    authorization = authorize_request(request, "project:read")
    response.headers["Cache-Control"] = "private, no-store"
    repository = project_repository(request)
    visible = [
        project
        for project in list_projects_use_case(repository)
        if project_visible_to_identity(project, request)
    ]
    latest_activity_by_project: dict[str, str] = {}
    if isinstance(repository, PostgresProjectRepository):
        verified_user = authorization.get("user") or {}
        tenant_id = str(verified_user.get("tenantId") or "").strip()
        user_id = str(verified_user.get("userId") or "").strip()
        if not tenant_id or not user_id:
            raise HTTPException(
                status_code=401,
                detail={"error": "verified_recent_work_identity_required"},
            )
        visible_project_ids = [
            str(
                (project.get("project") or {}).get("id")
                or project.get("id")
                or ""
            ).strip()
            for project in visible
        ]
        try:
            latest_activity_by_project = recent_project_activity_repository(
                tenant_id=tenant_id,
                user_id=user_id,
            ).latest_successful_project_activity(visible_project_ids)
        except RecentActivityRepositoryError as exc:
            raise HTTPException(
                status_code=503,
                detail={"error": "recent_activity_authority_unavailable"},
            ) from exc
    run_repository = orchestration_repository(request)
    visible_project_ids = [
        str(
            (
                project.get("project")
                if isinstance(project.get("project"), dict)
                else {}
            ).get("id")
            or project.get("id")
            or ""
        ).strip()
        for project in visible
    ]
    visible_project_ids = [
        project_id for project_id in visible_project_ids if project_id
    ]
    batch_latest = getattr(run_repository, "latest_metadata_by_project", None)
    if callable(batch_latest):
        latest_run_by_project = batch_latest(visible_project_ids)
    else:  # Compatibility for injected repositories during staged rollout.
        latest_run_by_project = {}
        for project_id in visible_project_ids:
            runs = run_repository.list_metadata(project_id)
            if runs:
                latest_run_by_project[project_id] = max(
                    runs,
                    key=lambda item: str(
                        item.get("completed_at")
                        or item.get("started_at")
                        or ""
                    ),
                )
    items: list[Dict[str, Any]] = []
    for project in visible:
        project_record = (
            project.get("project")
            if isinstance(project.get("project"), dict)
            else {}
        )
        project_id = str(project_record.get("id") or project.get("id") or "")
        if not project_id:
            continue
        # The project repository has already applied the verified request
        # tenant.  Read document metadata only from that authorized projection;
        # the process-global compatibility store is not a PostgreSQL authority.
        documents = list_source_documents_use_case(
            project_source_document_read_repository(project_id, project),
            project_id,
        )
        latest_run = latest_run_by_project.get(project_id, {})
        state = project.get("chief_orchestration_state") or {}
        gate = (
            state.get("approvalReadinessGate")
            or state.get("approval_readiness_gate")
            or {}
        )
        raw_approval_status = str(latest_run.get("approvalStatus") or "").strip().lower()
        approval_status = (
            raw_approval_status
            if raw_approval_status
            in {"not_requested", "pending", "approved", "rejected", "superseded"}
            else "not_requested"
        )
        risk_count = len(project.get("risk_register", {}).get("risks", []) if isinstance(project.get("risk_register"), dict) else [])
        open_review_count = len(project.get("pending_reviews", []) if isinstance(project.get("pending_reviews"), list) else [])
        raw_execution_status = (
            latest_run.get("executionStatus")
            or latest_run.get("status")
            or "not_started"
        )
        execution_status = str(raw_execution_status).strip().lower()
        next_action = _resume_action(project, documents, latest_run, gate)
        work_kind = _recent_work_kind(latest_run, approval_status)
        record_updated_at = _recent_work_updated_at(project, latest_run)
        activity_at = latest_activity_by_project.get(project_id)
        last_worked_at = activity_at or record_updated_at
        project_name = _project_display_name(project_id, project)
        items.append({
            # Additive v3 work-item fields.  The v2 fields below remain intact
            # for existing dashboards and mobile clients.
            "kind": work_kind,
            "id": str(latest_run.get("run_id") or project_id),
            "projectId": project_id,
            "title": project_name,
            "status": (
                approval_status
                if work_kind == "review"
                else execution_status
                if work_kind == "analysis"
                else str(project_record.get("status") or "draft")
            ),
            "executionStatus": execution_status,
            "approvalStatus": approval_status,
            "updatedAt": last_worked_at,
            "href": _recent_work_href(project_id, next_action),
            "summary": _recent_work_summary(
                work_kind=work_kind,
                execution_status=execution_status,
                approval_status=approval_status,
                document_count=len(documents),
                open_review_count=open_review_count,
                risk_count=risk_count,
            ),
            "projectName": project_name,
            "track": project.get("workflowTrack") or project.get("workflow_track"),
            "lastWorkedAt": last_worked_at,
            "lastActivityAt": activity_at,
            "recordUpdatedAt": record_updated_at,
            "latestRunId": latest_run.get("run_id"),
            "latestRunStatus": raw_execution_status if latest_run else None,
            "sourceDocumentCount": len(documents),
            "openReviewCount": open_review_count,
            "riskCount": risk_count,
            "approvalReadinessStatus": gate.get("status") or "not_started",
            "nextAction": next_action,
            "sourceHashes": sorted({str(item.get("sha256") or item.get("originalFileHash")) for item in documents if item.get("sha256") or item.get("originalFileHash")}),
        })
    items.sort(key=lambda item: str(item.get("lastWorkedAt") or ""), reverse=True)
    return {"schemaVersion": "realytics-recent-work-v2", "count": min(limit, len(items)), "items": items[:limit]}


@router.post("/intake/continuation-check")
def check_intake_continuation(request: Request, input_data: Dict[str, Any] | None = None):
    """Compare uploaded fingerprints with prior source documents before creating a new deal."""
    authorize_request(request, "project:read")
    files = input_data.get("files") if isinstance(input_data, dict) else []
    files = files if isinstance(files, list) else []
    visible = [p for p in list_projects_use_case(project_repository(request)) if project_visible_to_identity(p, request)]
    matches: list[Dict[str, Any]] = []
    for project in visible:
        project_id = str((project.get("project") or {}).get("id") or project.get("id") or "")
        documents = list_source_documents_use_case(
            project_source_document_read_repository(project_id, project),
            project_id,
        )
        fingerprints = {str(item.get("sha256") or item.get("originalFileHash")): item for item in documents if item.get("sha256") or item.get("originalFileHash")}
        matched_files = []
        for file in files:
            if not isinstance(file, dict):
                continue
            fingerprint = str(file.get("sha256") or "")
            if fingerprint and fingerprint in fingerprints:
                matched_files.append({"fileName": file.get("name"), "matchedDocument": fingerprints[fingerprint]})
        if matched_files:
            matches.append({"projectId": project_id, "projectName": _project_display_name(project_id, project), "matchedFiles": matched_files, "nextAction": "resume_existing_review"})
    return {"schemaVersion": "realytics-intake-continuation-check-v1", "matches": matches, "shouldAsk": bool(matches)}

@router.post("")
@router.post("/", include_in_schema=False)
def create_project(project_data: Dict[str, Any], request: Request):
    authorize_request(request, "project:write")
    reject_server_managed_project_authority_fields(project_data)
    try:
        repository = project_repository(request)
        if isinstance(repository, PostgresProjectRepository):
            context = identity_context_from_request(request)
            return repository.create(
                project_data,
                created_by=str((context.get("user") or {}).get("userId") or "") or None,
            )
        return create_project_use_case(repository, stamp_project_identity(project_data, request))
    except ProjectVersionConflict as exc:
        raise HTTPException(status_code=409, detail={"error": "project_version_conflict", "projectId": exc.project_id}) from exc

@router.get("/{project_id}")
def get_project(project_id: str, request: Request):
    authorize_request(request, "project:read")
    project = get_project_use_case(project_repository(request), project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    enforce_project_tenant_scope(request, project)
    return project

@router.put("/{project_id}")
def update_project(project_id: str, project_data: Dict[str, Any], request: Request):
    authorize_request(request, "project:write")
    reject_server_managed_project_authority_fields(project_data)
    current = get_project_use_case(project_repository(request), project_id)
    if not current:
        raise HTTPException(status_code=404, detail="Project not found")
    enforce_project_tenant_scope(request, current)
    try:
        project = update_project_use_case(project_repository(request), project_id, project_data)
    except ProjectVersionConflict as exc:
        raise HTTPException(status_code=409, detail={"error": "project_version_conflict", "projectId": exc.project_id, "actualStateVersion": exc.actual_state_version}) from exc
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project

@router.delete("/{project_id}")
def delete_project(project_id: str, request: Request):
    authorize_request(request, "project:write")
    current = get_project_use_case(project_repository(request), project_id)
    if not current:
        raise HTTPException(status_code=404, detail="Project not found")
    enforce_project_tenant_scope(request, current)
    try:
        deleted = delete_project_use_case(project_repository(request), project_id)
    except ProjectVersionConflict as exc:
        raise HTTPException(status_code=409, detail={"error": "project_version_conflict", "projectId": exc.project_id, "actualStateVersion": exc.actual_state_version}) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")
    delete_project_upload_archive_use_case(upload_archive_repository(), project_id)
    return {
        "deleted": True,
        "project_id": project_id,
        "project": deleted.get("project", {}),
    }

@router.post("/{project_id}/upload")
async def upload_business_plan(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
    x_realytics_user_id: str | None = Header(default=None),
    x_realytics_ai_provider: str | None = Header(default=None),
):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")

    ai_context = ai_context_from_request(
        request,
        explicit_provider=x_realytics_ai_provider,
        explicit_user_id=x_realytics_user_id,
    )
    ai_context = {**ai_context, "project_id": project_id, "agent_id": "ParserAgent"}
    parsed_data = await parse_uploaded_business_plan(project_id, file, ai_context)
    if parsed_data.get("_meta", {}).get("parsing_source") == "host_llm_required":
        upload_meta = parsed_data.get("_meta", {}).get("upload")
        if isinstance(upload_meta, dict):
            save_source_document_metadata_use_case(source_document_repository(), project_id, upload_meta)
        return {
            "project_id": project_id,
            "status": "host_llm_required",
            "upload": parsed_data,
        }

    project = apply_parsed_project_data_use_case(project_repository(), project_id, parsed_data)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    auto = _maybe_auto_reanalyze_on_upload(project_id, parsed_data)
    if auto:
        project = {**project, "autoReanalysis": auto}
    return project


def _maybe_auto_reanalyze(project_id: str, doc_types: list[str] | None) -> Dict[str, Any] | None:
    """Auto re-run the still-open / document-affected pods after new evidence — but
    only if the project was already analyzed (has completed pod artifacts). A
    brand-new project's first upload is left for the user's "전체 자동 실행".
    Never raises: auto re-analysis must not break the upload/persist request."""
    if project_id not in _project_store() or not _completed_pod_keys(project_id):
        return None
    try:
        return _reanalyze_after_evidence(project_id, changed_document_types=doc_types)
    except Exception:
        return {"triggered": True, "status": "failed", "cascadeComplete": False,
                "failureCode": "auto_reanalysis_failed"}


def _maybe_auto_reanalyze_on_upload(project_id: str, parsed_data: Dict[str, Any] | None) -> Dict[str, Any] | None:
    meta = (parsed_data or {}).get("_meta") or {}
    upload_meta = meta.get("upload") if isinstance(meta.get("upload"), dict) else {}
    doc_type = upload_meta.get("documentType") or meta.get("documentType") or "business_plan"
    return _maybe_auto_reanalyze(project_id, [str(doc_type)])

_INTAKE_NARRATIVE_TYPES = {"business_plan", "basic_architectural_plan", "investment_committee_memo"}


def _intake_type_mismatch(detected_format: str) -> HTTPException:
    message = (
        "PDF는 Excel 검증기로 처리할 수 없습니다. 원본을 문서 검토로 열어 주세요."
        if detected_format == "pdf"
        else "파일 형식이 선택한 처리 유형과 다릅니다. 문서 분류 결과에서 호환되는 처리 방법을 선택해 주세요."
    )
    return HTTPException(status_code=415, detail={
        "error": "intake_document_format_mismatch", "message": message,
    })


def _require_workbook_format(document_type: str, filename: str | None, header: bytes) -> None:
    """Reject known incompatible files before a workbook parser or archive write.

    A corrupt file with the expected workbook extension still goes through the
    existing validator so it can return its usual parse/repair guidance.
    """
    name = filename or ""
    declared = intake_processing_types(name)
    detected = sniff_intake_format(name, header)
    if document_type not in declared or (
        detected != "unknown" and document_type not in intake_processing_types(name, detected_format=detected)
    ):
        raise _intake_type_mismatch(detected)


async def _classify_intake_file(
    project_id: str, file: UploadFile, *, require_archive: bool = False,
) -> Dict[str, Any]:
    """Sniff + classify one uploaded file, archive the original, return the plan."""
    content = await file.read()
    name = file.filename or "upload"
    ctype = file.content_type
    archive = None
    archive_error = None
    # Scan the original before opening workbook/JSON content. Chunk transfers
    # require successful archival; their temporary parts are not evidence.
    try:
        archive = upload_archive_repository().archive_upload(
            project_id, content, name, ctype, tenant_id=_project_tenant_id(project_id),
        )
    except Exception as exc:
        if require_archive or isinstance(exc, UploadRejectedError):
            raise
        archive_error = type(exc).__name__
    detected_format = sniff_intake_format(name, content[:1024])
    sheet_names = None
    json_keys = None
    sample_text = None
    if archive is not None and detected_format in {"xlsx", "xlsm", "docx"}:
        try:
            safe_container, _, _ = inspect_office_container_safety(content)
        except Exception:
            safe_container = False
        if not safe_container:
            detected_format = "unknown"
    if archive is not None and detected_format in {"xlsx", "xlsm"}:
        try:
            import io
            import openpyxl  # type: ignore

            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
            sheet_names = list(wb.sheetnames)
            wb.close()
        except Exception:
            pass
    elif archive is not None and detected_format == "json" and len(content) <= 2 * 1024 * 1024:
        try:
            data = json.loads(content.decode("utf-8"))
            if isinstance(data, dict):
                json_keys = list(data.keys())
        except Exception:
            pass
    elif archive is not None and detected_format == "pdf":
        sample_text = await run_in_threadpool(read_intake_pdf_sample, content)

    classification = classify_document(
        name, content_type=ctype, size=len(content), sheet_names=sheet_names, json_keys=json_keys,
        sample_text=sample_text, detected_format=detected_format,
    )
    if detected_format == "pdf" and os.path.splitext(name)[1].lower() != ".pdf":
        classification["processingTypes"] = []
        classification["formatIssue"] = "extension_mismatch"
        classification["needsReview"] = True
        classification["reasons"] = [
            "파일 확장자와 실제 PDF 형식이 다릅니다. 원본의 파일 확장자를 확인해 주세요.",
            *classification["reasons"][:5],
        ]
    elif _INTAKE_NARRATIVE_TYPES.intersection(classification.get("processingTypes", [])):
        classification["processingMode"] = "document_review"
    try:
        if archive is None:
            classification["archiveStored"] = False
            classification["archiveError"] = archive_error
            return classification
        save_source_document_metadata_use_case(
            source_document_repository(),
            project_id,
            {
                **archive,
                "originalFileName": name,
                "fileName": name,
                "documentType": classification["documentType"],
                "documentLabel": classification["label"],
                "classifierConfidence": classification["confidence"],
                "uploadedVia": "smart_intake",
            },
        )
        classification["archiveId"] = archive.get("archiveId")
        classification["downloadUrl"] = archive.get("downloadUrl")
        classification["archiveStored"] = True
    except Exception as exc:  # storing must not break classification
        if require_archive:
            raise
        classification["archiveStored"] = False
        classification["archiveError"] = type(exc).__name__
    return classification


@router.post("/{project_id}/intake/classify")
async def smart_intake_classify(project_id: str, files: list[UploadFile] = File(...)):
    """스마트 업로드: 여러 자료를 한 번에 받아 파서 에이전트가 문서 유형을 자동
    분류하고 원본을 보관한다(사업계획서/토지대장/사업수지/토지증빙/시장자료 등)."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    results = [await _classify_intake_file(project_id, f) for f in files]
    by_type: Dict[str, int] = {}
    for r in results:
        by_type[r["documentType"]] = by_type.get(r["documentType"], 0) + 1
    return {
        "schemaVersion": "realytics-smart-intake-v1",
        "projectId": project_id,
        "count": len(results),
        "byType": by_type,
        "needsReviewCount": sum(1 for r in results if r.get("needsReview")),
        "files": results,
    }


def intake_transfer_store() -> IntakeTransferStore:
    return IntakeTransferStore(upload_archive_repository().object_storage)


def _intake_transfer_context(project_id: str, request: Request) -> Dict[str, str]:
    authorization = authorize_request(request, "project:write")
    project = get_project_use_case(project_repository(request), project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    enforce_project_tenant_scope(request, project)
    user = authorization.get("user") or {}
    if not authorization.get("authenticated") or not user.get("userId"):
        raise HTTPException(status_code=401, detail={"error": "authentication_required"})
    metadata = project.get("_meta") or {}
    tenant = metadata.get("tenantId") or metadata.get("tenant_id") or user.get("tenantId")
    if not tenant:
        raise HTTPException(status_code=403, detail={"error": "project_tenant_unassigned"})
    return {"projectId": project_id, "tenantId": str(tenant), "principalId": str(user["userId"])}


@router.post("/{project_id}/intake/transfers")
def create_intake_transfer(project_id: str, request: Request, input_data: Dict[str, Any]):
    scope = _intake_transfer_context(project_id, request)
    return intake_transfer_store().create(scope, input_data)


@router.put("/{project_id}/intake/transfers/{transfer_id}/parts/{index}")
async def upload_intake_transfer_part(project_id: str, transfer_id: str, index: int, request: Request):
    scope = _intake_transfer_context(project_id, request)
    store = intake_transfer_store()
    manifest = store.load(scope, transfer_id)
    limit = store.expected_part_size(manifest, index)
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/octet-stream":
        raise HTTPException(status_code=415, detail={"error": "intake_transfer_binary_part_required"})
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > limit:
            raise HTTPException(status_code=413, detail={"error": "intake_transfer_invalid_part_size"})
        content.extend(chunk)
    with store.operation(scope, transfer_id):
        return store.put_part(scope, transfer_id, index, bytes(content))


@router.post("/{project_id}/intake/transfers/{transfer_id}/classify")
async def classify_intake_transfer(project_id: str, transfer_id: str, request: Request):
    scope = _intake_transfer_context(project_id, request)
    store = intake_transfer_store()
    cached = store.read_result(scope, transfer_id, "classify")
    if cached is not None:
        return cached
    with store.operation(scope, transfer_id):
        cached = store.read_result(scope, transfer_id, "classify")
        if cached is not None:
            return cached
        with store.assembled_file(scope, transfer_id) as (manifest, content):
            file = UploadFile(file=content, filename=manifest["filename"],
                              headers=Headers({"content-type": manifest["contentType"]}))
            classified = await _classify_intake_file(project_id, file, require_archive=True)
        classified["transferId"] = transfer_id
        result = {
            "schemaVersion": "realytics-smart-intake-v1", "projectId": project_id,
            "count": 1, "byType": {classified["documentType"]: 1},
            "needsReviewCount": int(bool(classified.get("needsReview"))), "files": [classified],
        }
        store.save_result(scope, transfer_id, "classify", jsonable_encoder(result))
        return result


@router.post("/{project_id}/intake/transfers/{transfer_id}/process")
async def process_intake_transfer(
    project_id: str, transfer_id: str, request: Request, input_data: Dict[str, Any],
):
    scope = _intake_transfer_context(project_id, request)
    document_type = input_data.get("documentType")
    supported = {*_INTAKE_NARRATIVE_TYPES, "land_ledger",
                 "feasibility_workbook", "risk_report_workbook", "land_evidence"}
    if not isinstance(document_type, str) or document_type not in supported:
        raise HTTPException(status_code=422, detail={"error": "intake_transfer_document_type_unsupported"})
    store = intake_transfer_store()
    cached = store.read_result(scope, transfer_id, "process")
    if cached is not None:
        if cached["documentType"] != document_type:
            raise HTTPException(status_code=409, detail={"error": "intake_transfer_document_type_conflict"})
        return cached["result"]
    with store.operation(scope, transfer_id) as lease:
        cached = store.read_result(scope, transfer_id, "process")
        if cached is not None:
            if cached["documentType"] != document_type:
                raise HTTPException(status_code=409, detail={"error": "intake_transfer_document_type_conflict"})
            return cached["result"]
        with store.assembled_file(scope, transfer_id) as (manifest, content):
            detected = sniff_intake_format(manifest["filename"], content.read(1024))
            content.seek(0)
            if document_type not in intake_processing_types(manifest["filename"], detected_format=detected):
                raise _intake_type_mismatch(detected)
            if document_type == "investment_committee_memo" or (
                detected == "docx" and document_type in _INTAKE_NARRATIVE_TYPES
            ):
                raise HTTPException(status_code=422, detail={
                    "error": "intake_document_review_required",
                    "message": "저장된 원본으로 문서 검토를 시작해 주세요.",
                })
            file = UploadFile(file=content, filename=manifest["filename"],
                              headers=Headers({"content-type": manifest["contentType"]}))
            if document_type in {"business_plan", "basic_architectural_plan"}:
                result = await upload_business_plan(project_id, request, file,
                    request.headers.get("x-realytics-user-id"), request.headers.get("x-realytics-ai-provider"))
            elif document_type == "land_ledger":
                result = await analyze_land_ledger_upload(project_id, file)
            elif document_type == "feasibility_workbook":
                result = await validate_project_feasibility_workbook(project_id, file)
            elif document_type == "risk_report_workbook":
                result = await validate_project_risk_report_workbook(project_id, file)
            else:
                # Direct JSON processing also requires a verified archived
                # original when a prior classify request was not completed.
                await _classify_intake_file(project_id, file, require_archive=True)
                content.seek(0)
                try:
                    evidence = json.load(content)
                except (ValueError, UnicodeError) as exc:
                    raise HTTPException(status_code=422, detail={"error": "intake_transfer_invalid_json"}) from exc
                if isinstance(evidence, list):
                    evidence = {"records": evidence}
                if not isinstance(evidence, dict):
                    raise HTTPException(status_code=422, detail={"error": "intake_transfer_invalid_json"})
                result = upsert_project_land_evidence(project_id, request, evidence)
        if processing_succeeded(result):
            # The original may already have changed the project. If saving its
            # response fails, preserve the lease instead of replaying that
            # mutation. A persisted receipt remains readable before the lease.
            lease["retainOnExit"] = True
            try:
                store.save_result(scope, transfer_id, "process", jsonable_encoder({
                    "documentType": document_type, "result": result,
                }))
            except Exception as exc:
                raise HTTPException(status_code=503, detail={
                    "error": "intake_transfer_result_uncertain",
                    "message": "자료 처리 응답을 보관하지 못했습니다. 프로젝트의 저장 상태를 확인해 주세요.",
                }) from exc
            lease["retainOnExit"] = False
        return result


@router.delete("/{project_id}/intake/transfers/{transfer_id}")
def cancel_intake_transfer(project_id: str, transfer_id: str, request: Request):
    scope = _intake_transfer_context(project_id, request)
    return intake_transfer_store().cancel(scope, transfer_id)


@router.post("/{project_id}/intake/triage")
def smart_intake_triage(project_id: str, request: Request, input_data: Dict[str, Any] | None = None):
    """Recommend the OS business track after SmartIntake classification.

    The recommendation is based only on classified document metadata. It never
    treats the uploaded material as reviewed/approved evidence.
    """
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:write")
    project = dict(_project_store()[project_id])
    enforce_project_tenant_scope(request, project)
    payload = input_data or {}
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    override = payload.get("aiContext")
    # Ordinary hosted intake must be usable without a compatibility BYOK
    # profile or an external model. Explicit provider selection retains its
    # existing validation and authorization path.
    deterministic_only = storage_backend() in {"postgres", "postgresql"} and not override
    ai_context = None
    if not deterministic_only:
        ai_context = {
            **ai_context_from_request(request, payload_ai_context=override),
            "project_id": project_id,
            "agent_id": "IntakeTriageAgent",
        }
    result = {
        "projectId": project_id,
        "persisted": True,
        "confidenceBasis": "문서유형별 업무트랙 가중치 × 자동분류 신뢰도 + 파일명 신호를 기준으로 산정합니다. 승인 가능성은 별도 Approval Readiness Gate에서 판단합니다.",
        **triage_intake_documents(
            files,
            ai_context=ai_context,
            deterministic_only=deterministic_only,
        ),
    }
    history_item = {
        "triageId": str(uuid4()),
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "projectId": project_id,
        "recommendedTrack": result.get("recommendedTrack"),
        "recommendationSource": result.get("recommendationSource"),
        "aiAssisted": result.get("aiAssisted"),
        "confidenceBasis": result.get("confidenceBasis"),
        "inputSignals": result.get("inputSignals") or [],
        "tracks": result.get("tracks") or [],
        "limitations": result.get("limitations") or [],
    }
    history = list(project.get("intakeTriageHistory") or [])
    history.append(history_item)
    project["intakeTriageHistory"] = history[-20:]
    project["latestIntakeTriage"] = history_item
    _project_store()[project_id] = project
    persist_project(project_id)
    record_sensitive_project_event(
        request,
        event_type="intake_triage_completed",
        project_id=project_id,
        metadata={
            "recommendedTrack": result.get("recommendedTrack"),
            "recommendationSource": result.get("recommendationSource"),
            "aiAssisted": result.get("aiAssisted"),
            "fileCount": len(files),
        },
    )
    return result


@router.post("/{project_id}/intake/finalize")
def finalize_smart_intake(project_id: str, request: Request, input_data: Dict[str, Any] | None = None):
    """After classify+parse: promote identity, resolve address->PNU, and return
    a step-by-step readiness plan.

    PF review does not run by default. The user must explicitly trigger a review
    after source documents, land evidence, market evidence, feasibility inputs,
    and risk/cure decisions are sufficiently prepared.
    """
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:write")
    enforce_project_tenant_scope(request, _project_store()[project_id])
    payload = input_data or {}
    track = str(payload.get("track") or "")
    run = payload.get("run", False)

    finalize = finalize_project_intake_use_case(project_repository(), project_id, track=track)

    analysis_run = None
    if run is True and track in {"development", "pf"}:
        source_data = _hydrated_project_source(project_id, {})
        run_result = run_and_save_pf_review_use_case(
            orchestration_repository(request), project_id, source_data, {}
        )
        analysis_run = {
            "run_id": run_result.get("run_id"),
            "status": run_result.get("status"),
            "workflow": run_result.get("workflow"),
        }

    return {
        **finalize,
        "track": track,
        "analysisRun": analysis_run,
        **({"nextStage": "input_review"} if analysis_run is None and storage_backend() in {"postgres", "postgresql"} else {}),
    }


@router.put("/{project_id}/land-cost-review")
def save_land_cost_review(project_id: str, payload: Dict[str, Any], request: Request):
    project = authoritative_project(project_id, request)
    authorization = authorize_request(request, "project:review")
    enforce_project_tenant_scope(request, project)
    comment = payload.get("comment")
    if not isinstance(comment, str) or not comment.strip() or len(comment) > 5000:
        raise HTTPException(status_code=400, detail="검토 이유와 다음 확인 사항을 1~5,000자로 입력하세요.")
    comparison = reconcile_land_costs_use_case(_hydrated_project_source(project_id, {}, authoritative_source=project))
    if payload.get("expectedComparisonHash") != comparison["comparisonHash"]:
        raise HTTPException(status_code=409, detail="비교 원본이 변경됐습니다. 최신 금액을 다시 확인한 뒤 저장하세요.")
    if comparison["ledgerContract"]["value"] is None:
        raise HTTPException(status_code=400, detail="토지조서를 먼저 가져와 저장하세요.")
    previous = project.get("landCostReview") or {}
    # A note records the reviewer's reasoning; it grants no approval and never
    # changes any source amount, financial input or legal evidence.
    entry = {"comparisonHash": comparison["comparisonHash"], "comment": comment.strip(),
             "reviewedBy": verified_actor_id(authorization), "reviewedAt": now_iso(),
             "comparison": {key: value for key, value in comparison.items() if key not in {"review", "reviewMatchesCurrentSources"}}}
    review = {**entry, "reviewHistory": [*(previous.get("reviewHistory") or []), entry]}
    if not project_repository(request).update(project_id, {"landCostReview": review}):
        raise HTTPException(status_code=404, detail="Project not found")
    record_user_activity_log({"eventType": "land_cost_review_saved", "projectId": project_id,
                              "reviewedBy": entry["reviewedBy"], "createdAt": entry["reviewedAt"],
                              "comparisonHash": entry["comparisonHash"]})
    return {"projectId": project_id, "review": review}


@router.post("/{project_id}/land-ledger/analyze")
async def analyze_land_ledger_upload(
    project_id: str,
    file: UploadFile = File(...),
):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    ledger = await parse_uploaded_land_ledger(project_id, file)
    upload_meta = ledger.get("upload")
    if isinstance(upload_meta, dict):
        save_source_document_metadata_use_case(source_document_repository(), project_id, upload_meta)

    # Persist the parsed ledger onto the project so parcels are actually usable
    # downstream (batch PNU resolution, land pod, map) — previously the analysis
    # was returned to the caller and dropped.
    ledger_payload = {k: v for k, v in ledger.items() if k != "upload"}
    update_payload: Dict[str, Any] = {"landLedger": ledger_payload}
    parcels = ledger_payload.get("parcels") or []
    if parcels and not (_project_store()[project_id].get("land_acquisition") or []):
        update_payload["land_acquisition"] = parcels
    project_repository().update(project_id, update_payload)

    auto = _maybe_auto_reanalyze(project_id, ["land_ledger"])
    return {
        "project_id": project_id,
        "landLedger": ledger,
        "persisted": True,
        "autoReanalysis": auto,
        **({"landEvidenceEnrichment": "deferred"} if storage_backend() in {"postgres", "postgresql"} else {}),
        "note": "필지가 프로젝트에 저장되었습니다. /land/parcels/resolve로 지번별 PNU·공시지가를 일괄 해석할 수 있습니다.",
    }


@router.post("/{project_id}/land/parcels/resolve")
def resolve_project_parcel_pnus(project_id: str, request: Request, input_data: Dict[str, Any] | None = None):
    """토지조서의 모든 필지에 대해 지번별 PNU·공시지가를 VWorld로 일괄 해석·저장."""
    project = _project_store().get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:analyze")
    enforce_project_tenant_scope(request, project)
    payload = input_data or {}
    try:
        limit = max(1, min(500, int(payload.get("limit") or 150)))
    except (TypeError, ValueError):
        limit = 150
    result = resolve_project_parcels_use_case(project_repository(), project_id, limit=limit)
    project_after = _project_store().get(project_id) or {}
    parcels = canonical_project_parcel_rows(project_after)
    persisted_evidence: list[Dict[str, Any]] = []
    if payload.get("persistEvidence", True):
        for record in land_evidence_records_from_ledger({"parcels": parcels}):
            persisted_evidence.append(upsert_land_evidence_record(project_id, record))
    unresolved_count = len([
        row for row in result.get("results") or []
        if row.get("status") in {"unresolved", "no_address", "skipped_limit"}
    ])
    record_sensitive_project_event(
        request,
        event_type="parcel_pnu_batch_resolved",
        project_id=project_id,
        metadata={
            "resolvedCount": result.get("resolvedCount"),
            "attemptedCount": result.get("attemptedCount"),
            "totalParcels": result.get("totalParcels"),
            "unresolvedCount": unresolved_count,
            "persistedLandEvidenceCount": len(persisted_evidence),
        },
    )
    return {
        **result,
        "summary": {
            "attemptedCount": result.get("attemptedCount"),
            "resolvedCount": result.get("resolvedCount"),
            "unresolvedCount": unresolved_count,
            "totalParcels": result.get("totalParcels"),
            "limit": limit,
            "persistedLandEvidenceCount": len(persisted_evidence),
            "source": "VWorld address-to-parcel lookup",
        },
        "persistedLandEvidenceCount": len(persisted_evidence),
        "persistedLandEvidence": persisted_evidence,
        "vworld": vworld_status(),
        "autoReanalysis": (
            None if storage_backend() == "postgres"
            else _maybe_auto_reanalyze(project_id, ["land_ledger"])
        ),
    }

@router.post("/{project_id}/feasibility-workbook/validate")
async def validate_project_feasibility_workbook(
    project_id: str,
    file: UploadFile = File(...),
):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    result = await validate_uploaded_feasibility_workbook(project_id, file)
    save_source_document_metadata_use_case(source_document_repository(), project_id, result["upload"])
    return result


@router.post("/{project_id}/feasibility-workbook/extract")
async def extract_project_feasibility_workbook(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
    x_realytics_user_id: str | None = Header(default=None),
    x_realytics_ai_provider: str | None = Header(default=None),
    manual_review: bool = Query(False),
):
    """Archive a workbook for manual review or request optional LLM extraction.

    Returns a reviewable draft with per-field source-cell evidence + detected unit.
    Financial values are applied separately via /feasibility-workbook/apply-input.
    """
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:write")
    enforce_project_tenant_scope(request, _project_store()[project_id])
    suffix = os.path.splitext(file.filename or "")[1] or ".xlsx"
    if suffix.lower() not in {".xlsx", ".xlsm"}:
        raise HTTPException(status_code=400, detail=".xlsx 또는 .xlsm 파일로 저장해 올려 주세요.")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail={"error": "empty_file", "message": "빈 파일입니다."})
    archive_meta = archive_source_document_use_case(
        upload_archive_repository(),
        project_id,
        content,
        file.filename,
        file.content_type,
        tenant_id=_project_tenant_id(project_id),
    )
    upload_meta = {
        "originalFileName": file.filename,
        "originalFileHash": archive_meta["archiveId"],
        "documentType": "feasibility_workbook",
        "contentType": file.content_type,
        "fileSizeBytes": len(content),
        "uploadedAt": now_iso(),
        "parserVersion": "manual-workbook-review-v1" if manual_review else "feasibility-workbook-llm-extract-v1",
        **archive_meta,
    }
    save_source_document_metadata_use_case(source_document_repository(), project_id, upload_meta)
    if manual_review:
        return {"project_id": project_id, "upload": upload_meta, "parsingSource": "manual_review",
                "fields": {}, "analysisOnly": True, "reportEligible": False,
                "note": "원본을 보관했습니다. 출처 셀을 확인하고 필요한 값을 직접 입력하세요."}
    ai_context = ai_context_from_request(
        request,
        explicit_provider=x_realytics_ai_provider,
        explicit_user_id=x_realytics_user_id,
    )
    ai_context = {**ai_context, "project_id": project_id, "agent_id": "FeasibilityWorkbookLLMAgent"}
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        draft = extract_feasibility_fields_via_llm(tmp_path, ai_context)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return {"project_id": project_id, "upload": upload_meta, **draft}


@router.get("/{project_id}/feasibility-workbook/review")
def get_project_feasibility_review(project_id: str, request: Request):
    project = authoritative_project(project_id, request)
    authorize_request(request, "project:read")
    enforce_project_tenant_scope(request, project)
    return {**feasibility_review_state(project), "sourceDocuments": [
        {key: item.get(key) for key in ("archiveId", "originalFileName")}
        for item in source_document_repository().list(project_id)
        if item.get("archiveId") and str(item.get("originalFileName") or "").lower().endswith((".xlsx", ".xlsm"))
    ]}


@router.post("/{project_id}/feasibility-workbook/apply-input")
def apply_project_feasibility_input(project_id: str, request: Request, input_data: Dict[str, Any]):
    """Save reviewed analysis inputs without promoting them to approved evidence."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    # Working inputs remain needsReview/not_requested; formal workbook approval
    # below still requires assumption:override and its independent source gate.
    authz = authorize_request(request, "project:analyze")
    enforce_project_tenant_scope(request, _project_store()[project_id])
    source_document = input_data.get("sourceDocument") or {}
    if not isinstance(source_document, dict):
        raise HTTPException(status_code=400, detail="원본 문서 정보를 확인하세요.")
    archive_id = source_document.get("archiveId")
    if archive_id:
        stored_source = source_document_repository().find_by_archive_id(project_id, str(archive_id))
        if not stored_source:
            raise HTTPException(status_code=400, detail="이 프로젝트에 보관된 원본 문서가 아닙니다.")
        source_document = {"archiveId": archive_id, "originalFileName": stored_source.get("originalFileName")}
    else:
        source_document = None
    submitted_sources = input_data.get("fieldSourceDocuments") or {}
    if not isinstance(submitted_sources, dict):
        raise HTTPException(status_code=400, detail="항목별 원본 문서 정보를 확인하세요.")
    field_source_documents = {}
    for field, reference in submitted_sources.items():
        if not isinstance(reference, dict) or not reference.get("archiveId"):
            raise HTTPException(status_code=400, detail=f"{field}: 원본 문서를 선택하세요.")
        stored = source_document_repository().find_by_archive_id(project_id, str(reference["archiveId"]))
        if not stored:
            raise HTTPException(status_code=400, detail="이 프로젝트에 보관된 원본 문서가 아닙니다.")
        field_source_documents[field] = {"archiveId": stored["archiveId"], "originalFileName": stored.get("originalFileName")}
    if "terms" in input_data and not isinstance(input_data["terms"], dict):
        raise HTTPException(status_code=400, detail="사업기간·대출 조건 항목을 확인하세요.")
    submitted_term_sources = input_data.get("termSourceDocuments", {})
    if not isinstance(submitted_term_sources, dict) or submitted_term_sources.keys() - {"schedule", "financing"}:
        raise HTTPException(status_code=400, detail="사업기간·대출 조건 원본 문서를 확인하세요.")
    term_source_documents = {}
    for group, reference in submitted_term_sources.items():
        if not isinstance(reference, dict) or not reference.get("archiveId"):
            raise HTTPException(status_code=400, detail=f"{group}: 원본 문서를 선택하세요.")
        stored = source_document_repository().find_by_archive_id(project_id, str(reference["archiveId"]))
        if not stored:
            raise HTTPException(status_code=400, detail="이 프로젝트에 보관된 원본 문서가 아닙니다.")
        term_source_documents[group] = {"archiveId": stored["archiveId"], "originalFileName": stored.get("originalFileName")}
    feasibility_input = input_data.get("feasibility_input") or input_data.get("feasibilityInput") or {}
    try:
        result = apply_feasibility_input_use_case(
            project_repository(),
            project_id,
            feasibility_input,
            applied_by=user_id_from_request(request),
            source_document=source_document,
            field_reviews=input_data.get("fieldReviews"),
            field_source_documents=field_source_documents,
            expected_review_hash=input_data.get("expectedReviewHash"),
            terms=input_data.get("terms"),
            term_reviews=input_data.get("termReviews"),
            term_source_documents=term_source_documents,
        )
    except FeasibilityReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    record_sensitive_project_event(
        request,
        event_type="feasibility_input_applied",
        project_id=project_id,
        metadata={
            "appliedFields": list((result.get("appliedFields") or {}).keys()),
            "appliedTerms": result.get("appliedTerms") or [],
            "authorization": authz.get("authorization"),
        },
    )
    return result

@router.post("/{project_id}/feasibility-workbook/approve")
def approve_project_feasibility_workbook_import(
    project_id: str,
    request: Request,
    input_data: Dict[str, Any],
):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authz = authorize_request(request, "assumption:override")
    authenticated_actor = str(
        ((authz.get("user") or {}).get("userId") or user_id_from_request(request))
    )
    claimed_approver = str(
        input_data.get("approvedBy")
        or input_data.get("approved_by")
        or ""
    ).strip()
    if (
        authz.get("authenticated") is True
        and claimed_approver
        and claimed_approver != authenticated_actor
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "approval_actor_mismatch",
                "message": (
                    "approvedBy must match the authenticated reviewer identity"
                ),
            },
        )
    # Legacy permissive mode keeps the explicit actor only for local/offline
    # fixtures. Hardened deployments always bind approval to the verified
    # principal above, so callers cannot impersonate another reviewer.
    approved_by = (
        authenticated_actor
        if authz.get("authenticated") is True
        else claimed_approver or authenticated_actor
    )
    project_metadata = dict((_project_store().get(project_id) or {}).get("_meta") or {})
    tenant_id = str(
        tenant_id_from_request(request)
        or project_metadata.get("tenantId")
        or project_metadata.get("tenant_id")
        or "default"
    )
    try:
        result = approve_feasibility_workbook_import_use_case(
            project_repository(),
            project_id,
            input_data,
            approved_by=approved_by,
            known_source_documents=verified_workbook_approval_source_documents(project_id),
            parser_v2_record_store=parser_v2_record_store(),
            tenant_id=tenant_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_sensitive_project_event(
        request,
        event_type="feasibility_workbook_import_approved",
        project_id=project_id,
        metadata={
            "importId": result.get("importId"),
            "approvedBy": result.get("approval", {}).get("approvedBy"),
            "sourceDocument": result.get("approval", {}).get("sourceDocument"),
            "authorization": authz.get("authorization"),
        },
    )
    return result

@router.post("/{project_id}/feasibility-workbook/import-candidate")
def persist_project_feasibility_workbook_candidate(
    project_id: str,
    request: Request,
    input_data: Dict[str, Any],
):
    """Persist a reviewable workbook draft without changing Pod authority."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authz = authorize_request(request, "project:analyze")
    try:
        result = persist_feasibility_workbook_candidate_use_case(
            project_repository(),
            project_id,
            input_data,
            recorded_by=user_id_from_request(request),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    auto = {"triggered": False, "reason": "candidate_requires_explicit_approval"}
    result["autoReanalysis"] = auto
    record_sensitive_project_event(
        request,
        event_type="feasibility_workbook_candidate_persisted",
        project_id=project_id,
        metadata={
            "importId": result.get("importId"),
            "status": result.get("status"),
            "authorization": authz.get("authorization"),
            "autoReanalysisTriggered": bool(auto.get("triggered")),
        },
    )
    return result


@router.post("/{project_id}/feasibility-workbook/learn")
async def learn_project_feasibility_workbook(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    identity = authorize_request(request, "project:analyze")
    user = identity.get("user") or {}
    result = await learn_uploaded_feasibility_workbook(
        project_id,
        file,
        tenant_id=str(user.get("tenantId") or "default"),
        created_by=str(user.get("userId") or "unknown"),
    )
    save_source_document_metadata_use_case(source_document_repository(), project_id, result["upload"])
    return result

@router.post("/{project_id}/risk-report-workbook/validate")
async def validate_project_risk_report_workbook(
    project_id: str,
    file: UploadFile = File(...),
):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    result = await validate_uploaded_risk_report_workbook(project_id, file)
    save_source_document_metadata_use_case(source_document_repository(), project_id, result["upload"])
    return result

@router.post("/{project_id}/risk-report-workbook/approve")
def approve_project_risk_report_workbook_import(
    project_id: str,
    request: Request,
    input_data: Dict[str, Any],
):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authz = authorize_request(request, "risk:review")
    try:
        result = approve_risk_report_workbook_import_use_case(
            project_repository(),
            project_id,
            input_data,
            approved_by=input_data.get("approvedBy")
            or input_data.get("approved_by")
            or user_id_from_request(request),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_sensitive_project_event(
        request,
        event_type="risk_report_workbook_import_approved",
        project_id=project_id,
        run_id=result.get("importId"),
        metadata={
            "importId": result.get("importId"),
            "riskCount": (result.get("riskRegister") or {}).get("riskCount"),
            "approvedBy": result.get("approval", {}).get("approvedBy"),
            "sourceDocument": result.get("approval", {}).get("sourceDocument"),
            "authorization": authz.get("authorization"),
        },
    )
    return result

@router.post("/{project_id}/land-ledger/enrich")
def enrich_project_land_ledger(project_id: str, input_data: Dict[str, Any] | None = None):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")

    input_data = input_data or {}
    land_ledger = input_data.get("landLedger") or _project_store()[project_id].get("landLedger")
    if not land_ledger:
        raise HTTPException(status_code=400, detail="landLedger payload or saved project landLedger is required")

    options = land_ledger_auto_enrichment_options(
        project_id,
        land_ledger,
        input_data.get("options", {}),
    )
    enriched = enrich_land_ledger_use_case(land_ledger, options)
    if options.get("autoPnuDiagnostics"):
        enriched.setdefault("enrichmentRun", {})
        enriched["enrichmentRun"]["autoPnuDiagnostics"] = options["autoPnuDiagnostics"]
    if input_data.get("persist", False):
        project = dict(_project_store()[project_id])
        project["landLedger"] = enriched
        updated = update_project_use_case(project_repository(), project_id, project)
        if not updated:
            raise HTTPException(status_code=404, detail="Project not found")
    persisted_evidence = []
    if input_data.get("persistEvidence") or input_data.get("persist_evidence") or input_data.get("persist", False):
        for record in land_evidence_records_from_ledger(enriched):
            persisted_evidence.append(upsert_land_evidence_record(project_id, record))
    return {
        "project_id": project_id,
        "landLedger": enriched,
        "persistedLandEvidenceCount": len(persisted_evidence),
        "persistedLandEvidence": persisted_evidence,
        "vworld": vworld_status(),
        "note": "PNU requires a matching VWorld cadastral parcel or a supplied parcel identifier. A supplied legal dong code only generates a lookup candidate.",
    }

@router.post("/{project_id}/upload-and-run")
async def upload_business_plan_and_run_pf_review(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
    x_realytics_user_id: str | None = Header(default=None),
    x_realytics_ai_provider: str | None = Header(default=None),
):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")

    ai_context = ai_context_from_request(
        request,
        explicit_provider=x_realytics_ai_provider,
        explicit_user_id=x_realytics_user_id or "web",
    )
    ai_context = {**ai_context, "project_id": project_id, "agent_id": "ParserAgent"}
    parsed_data = await parse_uploaded_business_plan(project_id, file, ai_context)
    if parsed_data.get("_meta", {}).get("parsing_source") == "host_llm_required":
        upload_meta = parsed_data.get("_meta", {}).get("upload")
        if isinstance(upload_meta, dict):
            save_source_document_metadata_use_case(source_document_repository(), project_id, upload_meta)
        return {
            "project_id": project_id,
            "status": "host_llm_required",
            "upload": parsed_data,
            "message": "Host LLM extraction is required before PF review orchestration can run.",
        }

    project_data = apply_parsed_project_data_use_case(project_repository(), project_id, parsed_data)
    if not project_data:
        raise HTTPException(status_code=404, detail="Project not found")
    project_data = attach_persisted_land_evidence(project_id, project_data)
    project_data = attach_approved_assumption_overrides(project_id, project_data)
    project_data = attach_active_restructuring_cures(project_id, project_data)
    run = run_and_save_pf_review_use_case(
        orchestration_repository(request),
        project_id,
        project_data,
        {
            "requested_by": ai_context["user_id"],
            "user_id": ai_context["user_id"],
            "ai_context": ai_context,
            "source": "upload-and-run",
        },
    )
    return {
        "project_id": project_id,
        "status": "completed",
        "project": project_data,
        "run": run,
    }

@router.post("/{project_id}/ai/connection/resolve")
def resolve_project_ai_connection(project_id: str, request: Request, input_data: Dict[str, Any] | None = None):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    input_data = input_data or {}
    ai_context = ai_context_from_request(
        request,
        explicit_user_id=input_data.get("user_id"),
        payload_ai_context=input_data.get("ai_context", input_data),
    )
    return {
        "project_id": project_id,
        "ai_connection": safe_user_ai_context_summary(ai_context),
        "note": (
            "Claude Desktop hosts MCP tool calls. Direct Claude extraction inside the API "
            "requires ANTHROPIC_API_KEY."
        ),
    }

@router.post("/{project_id}/feasibility/calculate")
def calculate_feasibility(project_id: str, input_data: Dict[str, Any]):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    project_feasibility = _project_store()[project_id].get("feasibility_input", {})
    source_data = deep_merge(project_feasibility, input_data or {})
    result = calculate_feasibility_metrics(source_data)
    run = _persist_calculation_run(project_id, engine_key="feasibility", result=result,
                                  input_data=source_data, effective_input_captured=True)
    return {**result, "calculationRunId": run["calculationRunId"]}


@router.get("/{project_id}/calculation-runs")
def list_project_calculation_runs(
    project_id: str,
    request: Request,
    engine_key: str | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    project = authoritative_project(project_id, request)
    authorize_request(request, "project:read")
    enforce_project_tenant_scope(request, project)
    try:
        if storage_backend() in {"postgres", "postgresql"}:
            tenant_id = str(tenant_id_from_request(request) or "").strip()
            if not tenant_id:
                raise HTTPException(
                    status_code=401,
                    detail={"error": "tenant_required"},
                )
            records = PostgresCalculationRunRepository(
                tenant_id=tenant_id
            ).list_runs(
                project_id,
                engine_key=engine_key,
                limit=limit,
            )
        else:
            records = list_calculation_run_records(
                project_id,
                engine_key=engine_key,
                limit=limit,
            )
    except HTTPException:
        raise
    except CalculationRunRepositoryError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "calculation_run_authority_unavailable"},
        ) from exc
    return {
        "schemaVersion": "realytics-calculation-run-list-v2",
        "projectId": project_id,
        "engineKey": engine_key,
        "count": len(records),
        "records": records,
    }


def _cashflow_export_selection(
    project_id: str,
    request: Request,
    *,
    anchor_calculation_run_id: str | None = None,
) -> Dict[str, Any]:
    dependent_engine_keys = CASHFLOW_EXPORT_ENGINE_KEYS[1:]
    try:
        if storage_backend() in {"postgres", "postgresql"}:
            tenant_id = str(tenant_id_from_request(request) or "").strip()
            if not tenant_id:
                raise HTTPException(
                    status_code=401,
                    detail={"error": "tenant_required"},
                )
            return PostgresCalculationRunRepository(
                tenant_id=tenant_id
            ).select_export_runs(
                project_id,
                dependent_engine_keys=dependent_engine_keys,
                anchor_calculation_run_id=anchor_calculation_run_id,
            )
        return select_local_export_runs(
            list_calculation_run_records(project_id, limit=500),
            dependent_engine_keys=dependent_engine_keys,
            anchor_calculation_run_id=anchor_calculation_run_id,
        )
    except HTTPException:
        raise
    except CalculationRunRepositoryError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "calculation_run_authority_unavailable"},
        ) from exc


def _require_complete_cashflow_export_lineage(
    selection: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    anchor = selection.get("anchor")
    missing = list(selection.get("missingEngineKeys") or [])
    ineligible = list(selection.get("ineligibleEngineKeys") or [])
    if not isinstance(anchor, dict):
        missing = list(CASHFLOW_EXPORT_ENGINE_KEYS)
    if (
        not isinstance(anchor, dict)
        or selection.get("reportEligible") is not True
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "cashflow_export_lineage_incomplete",
                "reason": (
                    "anchor_missing"
                    if not isinstance(anchor, dict)
                    else "dependent_run_lineage_incomplete"
                ),
                "reportEligible": False,
                "anchorCalculationRunId": (
                    anchor.get("calculationRunId")
                    if isinstance(anchor, dict)
                    else None
                ),
                "missingEngineKeys": missing,
                "ineligibleEngineKeys": ineligible,
            },
        )
    runs = selection.get("runs")
    if not isinstance(runs, dict) or any(
        not isinstance(runs.get(engine_key), dict)
        for engine_key in CASHFLOW_EXPORT_ENGINE_KEYS
    ):
        raise HTTPException(
            status_code=503,
            detail={"error": "calculation_run_authority_unavailable"},
        )
    return anchor, runs


def _cashflow_export_policy(project_id: str, request: Request, runs: Dict[str, Any], purpose: str):
    from use_cases.approval_readiness_gate import build_deal_state_approval_readiness
    from use_cases.cashflow_export_policy import evaluate_cashflow_export_policy

    policy = evaluate_cashflow_export_policy(
        runs, purpose,
        lambda: build_deal_state_approval_readiness(project_id, tenant_id=tenant_id_from_request(request)),
    )
    if not policy["downloadEligible"]:
        raise HTTPException(status_code=409, detail={"error": "cashflow_approved_export_not_ready", **policy})
    return policy


@router.get("/{project_id}/cashflow-export-package")
def get_project_cashflow_export_package(project_id: str, request: Request, purpose: str = Query("draft", pattern="^(draft|approved)$")):
    project = authoritative_project(project_id, request)
    authorization = authorize_request(request, "report:export")
    enforce_project_tenant_scope(request, project)
    actor_id = verified_actor_id(authorization)
    selection = _cashflow_export_selection(project_id, request)
    anchor, runs_by_engine = _require_complete_cashflow_export_lineage(selection)
    policy = _cashflow_export_policy(project_id, request, runs_by_engine, purpose)
    record_sensitive_project_event(
        request,
        event_type="cashflow_export_package_read",
        project_id=project_id,
        metadata={
            "exportType": "cashflow_calculation_package",
            "exportPurpose": purpose,
            "requestedBy": actor_id,
            "anchorCalculationRunId": anchor.get("calculationRunId"),
            "engineKeys": list(CASHFLOW_EXPORT_ENGINE_KEYS),
            "calculationRunIds": {
                engine_key: record.get("calculationRunId")
                for engine_key, record in runs_by_engine.items()
            },
            "authorization": authorization.get("authorization"),
        },
    )
    return {
        "schemaVersion": "realytics-cashflow-export-package-v1",
        "projectId": project_id,
        "generatedAt": now_iso(),
        "generatedBy": actor_id,
        "anchorCalculationRunId": anchor.get("calculationRunId"),
        **policy,
        "engineKeys": list(CASHFLOW_EXPORT_ENGINE_KEYS),
        "missingEngineKeys": [],
        "ineligibleEngineKeys": [],
        "runs": runs_by_engine,
    }


@router.post("/{project_id}/cashflow-export-completions", status_code=201)
def complete_project_cashflow_export(
    project_id: str,
    request: Request,
    completion: CashflowExportCompletionRequest,
):
    project = authoritative_project(project_id, request)
    authorization = authorize_request(request, "report:export")
    enforce_project_tenant_scope(request, project)
    actor_id = verified_actor_id(authorization)
    selection = _cashflow_export_selection(
        project_id,
        request,
        anchor_calculation_run_id=completion.anchor_calculation_run_id,
    )
    anchor, runs_by_engine = _require_complete_cashflow_export_lineage(selection)
    expected_ids = {
        engine_key: str(record.get("calculationRunId") or "")
        for engine_key, record in runs_by_engine.items()
    }
    if expected_ids != completion.calculation_run_ids:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "cashflow_export_completion_lineage_mismatch",
                "anchorCalculationRunId": anchor.get("calculationRunId"),
            },
        )
    policy = _cashflow_export_policy(project_id, request, runs_by_engine, completion.export_purpose)
    completion_id = str(uuid4())
    completed_at = now_iso()
    record_sensitive_project_event(
        request,
        event_type="cashflow_export_completed",
        project_id=project_id,
        metadata={
            "completionId": completion_id,
            "exportType": "cashflow_xlsx",
            "exportPurpose": completion.export_purpose,
            "reportEligible": policy["reportEligible"],
            "completedBy": actor_id,
            "completedAt": completed_at,
            "anchorCalculationRunId": anchor.get("calculationRunId"),
            "calculationRunIds": expected_ids,
            "artifactSha256": completion.artifact_sha256,
            "fileName": completion.file_name,
            "sizeBytes": completion.size_bytes,
            "mediaType": completion.media_type,
            "artifactVerification": "client_generated_metadata_attested",
            "authorization": authorization.get("authorization"),
        },
    )
    return {
        "schemaVersion": "realytics-cashflow-export-completion-v1",
        "completionId": completion_id,
        "projectId": project_id,
        "completedAt": completed_at,
        "completedBy": actor_id,
        "anchorCalculationRunId": anchor.get("calculationRunId"),
        "calculationRunIds": expected_ids,
        "artifactSha256": completion.artifact_sha256,
        "fileName": completion.file_name,
        "sizeBytes": completion.size_bytes,
        "mediaType": completion.media_type,
    }


@router.get("/{project_id}/deal-state-records")
def list_project_deal_state_records(
    project_id: str,
    request: Request,
    record_type: str | None = None,
    calculation_run_id: str | None = None,
    review_status: str | None = None,
    missing_locator: bool | None = None,
    latest_only: bool = False,
    limit: int = Query(200, ge=1, le=1000),
):
    """List Deal State records.

    Filters for the reviewer remediation workflow:
      - ``review_status``: exact match; ``unreviewed`` matches records with no
        reviewStatus set yet.
      - ``missing_locator=true``: only records lacking a usable source locator
        (the "근거 위치 부족" set the reviewer needs to supplement);
        ``false`` returns only records that already have a locator.

    The response always carries a project-wide ``completenessSummary`` computed
    over the record-type scope (ignoring the locator/review filters) so the UI
    can show "N of M records need a source locator" alongside the filtered view.
    """
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    records = list_deal_state_records(
        project_id,
        record_type=record_type,
        calculation_run_id=calculation_run_id,
        review_status=review_status,
        missing_locator=missing_locator,
        latest_only=latest_only,
        limit=limit,
    )
    # Completeness summary over the same record-type scope, independent of the
    # locator/review filters, so the badge total does not shift as the reviewer
    # narrows the view.
    scope = list_deal_state_records(
        project_id,
        record_type=record_type,
        calculation_run_id=calculation_run_id,
        latest_only=latest_only,
        limit=1000,
    )
    with_locator = [r for r in scope if _deal_state_record_has_source_locator(r)]
    reviewed = [r for r in scope if r.get("reviewStatus")]
    return {
        "schemaVersion": "realytics-deal-state-record-list-v1",
        "projectId": project_id,
        "recordType": record_type,
        "calculationRunId": calculation_run_id,
        "reviewStatus": review_status,
        "missingLocator": missing_locator,
        "count": len(records),
        "completenessSummary": {
            "scopeCount": len(scope),
            "withSourceLocatorCount": len(with_locator),
            "missingSourceLocatorCount": len(scope) - len(with_locator),
            "reviewedCount": len(reviewed),
            "unreviewedCount": len(scope) - len(reviewed),
        },
        "records": records,
    }


@router.get("/{project_id}/parsed-facts")
def list_project_parsed_facts(
    project_id: str,
    request: Request,
    pod_key: str | None = None,
    validation_status: str | None = None,
    limit: int = Query(1000, ge=1, le=1000),
):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    package = parsed_fact_package(project_id, limit=limit)
    records = package.get("records") or []
    if pod_key:
        records = [record for record in records if pod_key in {str(p) for p in record.get("targetPods") or []}]
    if validation_status:
        records = [record for record in records if record.get("validationStatus") == validation_status]
    return {
        **package,
        "podKey": pod_key,
        "validationStatus": validation_status,
        "count": len(records),
        "records": records,
    }


@router.post("/{project_id}/parsed-facts/validate-pod")
def validate_project_parsed_facts_for_pod(
    project_id: str,
    payload: Dict[str, Any],
    request: Request,
):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:review")
    pod_key = str(payload.get("podKey") or payload.get("pod_key") or "").strip()
    if not pod_key:
        raise HTTPException(status_code=400, detail="podKey is required")
    actor = str(
        payload.get("validatedBy")
        or payload.get("validated_by")
        or identity_context_from_request(request).get("userId")
        or "pod_fact_validator"
    )
    result = _validate_supplement_and_revalidate_parsed_facts(
        project_id,
        pod_key,
        project_data=_project_store()[project_id],
        source_artifact_key=payload.get("sourceArtifactKey") or payload.get("source_artifact_key"),
        actor=actor,
    )
    record_user_activity_log(
        {
            "eventType": "parsed_fact_pod_validated",
            "projectId": project_id,
            "podKey": pod_key,
            "validatedBy": actor,
            "findingCount": (result.get("summary") or {}).get("findingCount"),
            "riskIssueCount": (result.get("summary") or {}).get("riskIssueCount"),
            "createdAt": result.get("validatedAt"),
        }
    )
    return result


@router.patch("/{project_id}/deal-state-records/{record_id}/review")
def review_project_deal_state_record(
    project_id: str,
    record_id: str,
    payload: Dict[str, Any],
    request: Request,
):
    project = authoritative_project(project_id, request)
    authorization = authorize_request(request, "project:review")
    enforce_project_tenant_scope(request, project)
    reviewer = verified_actor_id(authorization)
    review_status = str(payload.get("reviewStatus") or payload.get("review_status") or "").strip()
    if review_status not in {"approved", "waived", "needs_revision", "resolved"}:
        raise HTTPException(status_code=400, detail="reviewStatus must be approved, waived, needs_revision, or resolved")
    records = list_deal_state_records(project_id, limit=1000)
    record = next((item for item in records if item.get("recordId") == record_id), None)
    if not record:
        raise HTTPException(status_code=404, detail="Deal State record not found")

    now = datetime.now(timezone.utc).isoformat()
    audit_entry = {
        "reviewStatus": review_status,
        "reviewedBy": reviewer,
        "reviewedAt": now,
        "comment": payload.get("comment"),
        "waiverReason": payload.get("waiverReason") or payload.get("waiver_reason"),
    }
    updated = {
        **record,
        "reviewStatus": review_status,
        "reviewedBy": reviewer,
        "reviewedAt": now,
        "comment": payload.get("comment") or record.get("comment"),
        "waiverReason": payload.get("waiverReason") or payload.get("waiver_reason") or record.get("waiverReason"),
        "reviewHistory": [*(record.get("reviewHistory") or []), audit_entry],
    }
    saved = upsert_deal_state_record(project_id, updated)
    record_sensitive_project_event(
        request,
        event_type="deal_state_record_reviewed",
        project_id=project_id,
        metadata={
            "recordId": record_id,
            "recordType": saved.get("recordType"),
            "reviewStatus": review_status,
            "reviewedBy": reviewer,
            "createdAt": now,
        }
    )
    return {
        "schemaVersion": "realytics-deal-state-record-review-v1",
        "projectId": project_id,
        "record": saved,
    }


@router.post("/{project_id}/deal-state-records/source-locator-audit")
def audit_project_source_locator_completeness(
    project_id: str,
    request: Request,
):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:review")
    actor = identity_context_from_request(request).get("userId") or "source_locator_auditor"
    result = run_source_locator_completeness_audit_use_case(
        project_id,
        report_records=REPORTS_DB.get(project_id, []),
        actor=str(actor),
    )
    record_user_activity_log(
        {
            "eventType": "source_locator_completeness_audited",
            "projectId": project_id,
            "auditId": result.get("auditId"),
            "checkedDealStateRecordCount": result.get("checkedDealStateRecordCount"),
            "missingLocatorCount": result.get("missingLocatorCount"),
            "weakTraceCount": result.get("weakTraceCount"),
            "weakParagraphCount": result.get("weakParagraphCount"),
            "createdAt": result.get("checkedAt"),
            "userId": actor,
        }
    )
    return result


@router.post("/{project_id}/development-feasibility/analyze")
def analyze_development_feasibility(project_id: str, input_data: Dict[str, Any] | None = None):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    payload = input_data or {}
    input_basis = development_feasibility_input_basis(_project_store()[project_id], payload)
    result = analyze_development_feasibility_use_case(input_basis)
    run = _persist_calculation_run(
        project_id,
        engine_key="feasibility",
        result=result,
        input_data={
            **payload,
            **input_basis,
            "engineVersion": result["engineVersion"],
            "sourceLocator": payload.get("sourceLocator") or {
                "sourceType": "project_feasibility_input",
                "projectId": project_id,
            },
        },
        effective_input_captured=True,
    )
    return {**result, "calculationRunId": run["calculationRunId"]}


@router.post("/{project_id}/asset-comparison/analyze")
def analyze_asset_feasibility_comparison(
    project_id: str,
    request: Request,
    input_data: Dict[str, Any] | None = None,
):
    """Compare developer feasibility across asset-type scenarios for the same land.

    Deterministic on-demand analysis (no persistence): each scenario is a re-run
    of the feasibility engine under asset-type overrides. Scenarios without a
    revenue/cost basis are surfaced as ``insufficient_data``, never fabricated.
    """
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    payload = input_data or {}
    scenarios = payload.get("scenarios") if isinstance(payload.get("scenarios"), list) else None
    options = payload.get("options") if isinstance(payload.get("options"), dict) else None
    return compare_asset_feasibility_use_case(_project_store()[project_id], scenarios, options)


@router.post("/{project_id}/covenant-eod/evaluate")
def evaluate_covenant_eod(
    project_id: str,
    request: Request,
    input_data: Dict[str, Any] | None = None,
):
    """Derive covenant thresholds + EOD conditions from the deal's deterministic
    feasibility/PF numbers (목표 분양률·LTV/LTC·책임준공·Cash Sweep·상환계좌 배분·
    Equity 선투입·공사비 초과). Each item carries a source locator; missing inputs
    are surfaced as needs_evidence, never fabricated. On-demand, no persistence."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    payload = input_data or {}
    overrides = payload.get("overrides") if isinstance(payload.get("overrides"), dict) else None
    policy = payload.get("policy") if isinstance(payload.get("policy"), dict) else None
    return evaluate_covenant_eod_use_case(_project_store()[project_id], overrides, policy)


@router.post("/{project_id}/product-planning/compare")
def compare_product_planning(
    project_id: str,
    request: Request,
    input_data: Dict[str, Any] | None = None,
):
    """Compare an indicative development program across asset types for the same
    land (연면적·전용면적·세대/호실수·전용률·주차·분양가·임대료). Deterministic;
    planning ratios are labelled ``planning_benchmark`` and overridable, and any
    revenue/rent without a price basis is left unset, never fabricated."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    payload = input_data or {}
    scenarios = payload.get("scenarios") if isinstance(payload.get("scenarios"), list) else None
    options = payload.get("options") if isinstance(payload.get("options"), dict) else None
    return compare_product_planning_use_case(_project_store()[project_id], scenarios, options)


@router.post("/{project_id}/market-product-depth/analyze")
def analyze_market_product_depth(
    project_id: str,
    request: Request,
    input_data: Dict[str, Any] | None = None,
):
    """Market-Product depth: 제공주체(시행사/매각주관사/공공/AI) 분류·비교, 상품유형별
    분양성 Score, 흡수율 모델, AI 추가필요작업 판단. Deterministic numbers; the LLM
    only judges what additional evidence/analysis is needed (fallback deterministic)."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    payload = input_data or {}
    options = payload.get("options") if isinstance(payload.get("options"), dict) else None
    return analyze_market_product_depth_use_case(_project_store()[project_id], options)


@router.post("/{project_id}/feasibility/build-input")
def build_project_feasibility_input(project_id: str, request: Request, input_data: Dict[str, Any] | None = None):
    """AI-assisted 사업수지 구성: pull feasibility_input (총수입·총사업비·공사비·토지비·
    금융비·PF) from parsed 사업수지 sources (feasibilityStudyV2 / parsed_facts) — and,
    where a source is missing, propose deterministic values from land·regulation·
    product plan·market price·공시지가 (flagged needsReview). With apply=true it
    persists into feasibility_input so Cash Flow / PF produce real numbers. Never
    fabricates a missing value to a made-up number."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:write")
    payload = input_data or {}
    try:
        parsed_facts = list_deal_state_records(project_id, record_type="parsed_fact")
    except Exception:
        parsed_facts = []
    options = payload.get("options") if isinstance(payload.get("options"), dict) else None
    draft = build_feasibility_input_draft(_project_store()[project_id], parsed_facts=parsed_facts, options=options)
    apply = payload.get("apply", True)
    if apply and draft.get("feasibilityInput"):
        project = _project_store()[project_id]
        feasibility_input = dict(project.get("feasibility_input") or {})
        for key, value in draft["feasibilityInput"].items():
            feasibility_input[key] = value
        feasibility_input.setdefault("schema_source", "feasibility_input_builder")
        update_payload: Dict[str, Any] = {
            "feasibility_input": feasibility_input,
            "feasibility_input_sources": {
                key: {**source, "value": draft["feasibilityInput"].get(key)}
                for key, source in (draft.get("fieldSources") or {}).items()
            },
        }
        # Materialize land area from the parsed program so map/land/feasibility engines
        # read it (it was extracted into parsed_facts but not the structured land field).
        land_area = ((draft.get("programBreakdown") or {}) or {}).get("landAreaSqm")
        if land_area and not (project.get("land") or {}).get("area_sqm"):
            land = dict(project.get("land") or {})
            land["area_sqm"] = land_area
            update_payload["land"] = land
        # Materialize the architectural program + product mix from the approved 사업수지
        # workbook (feasibilityStudyV2) so the feasibility engine + Pod AI review see the
        # GFA/product mix (otherwise architecturalProgram.grossFloorAreaSqm stays 0 and
        # the pod flags "건축 프로그램 누락" even though the workbook has it).
        v2 = project.get("feasibilityStudyV2") or project.get("canonicalFeasibilityStudyV2") or {}
        if isinstance(v2, dict):
            program = v2.get("program") or {}
            gfa_node = program.get("gross_floor_area_sqm") if isinstance(program, dict) else None
            gfa_val = gfa_node.get("value") if isinstance(gfa_node, dict) else (gfa_node if isinstance(gfa_node, (int, float)) else None)
            if gfa_val and not (project.get("architectural_plan") or {}).get("gross_floor_area_sqm"):
                arch = dict(project.get("architectural_plan") or {})
                arch["gross_floor_area_sqm"] = gfa_val
                update_payload["architectural_plan"] = arch
            canonical_mix = canonical_product_mix_to_runtime(v2.get("product_mix"))
            if canonical_mix:
                # The approved canonical workbook is authoritative for the
                # downstream runtime shape as well. Preserve provenance on each
                # row so later Pod reconciliation can distinguish it from a
                # manually entered product mix.
                update_payload["product_mix"] = canonical_mix
                update_payload["product_mix_source"] = "canonical_feasibility_study_v2"
        project_repository().update(project_id, update_payload)
        draft["applied"] = True
        record_sensitive_project_event(
            request,
            event_type="feasibility_input_built",
            project_id=project_id,
            metadata={"method": draft.get("method"), "needsReview": draft.get("needsReview"), "missing": draft.get("missing")},
        )
    else:
        draft["applied"] = False
    return draft

@router.post("/{project_id}/cashflow/calculate")
def calculate_cashflow(project_id: str, input_data: Dict[str, Any]):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    cfs = input_data.get("cashflows", [])
    max_minus = calculate_cashflow_max_minus(cfs)
    result = build_monthly_cash_flow(input_data)
    result["cashflows"] = cfs
    result["legacy_max_minus"] = max_minus
    run = _persist_calculation_run(project_id, engine_key="cashflow", result=result,
                                  input_data=input_data, effective_input_captured=True)
    return {**result, "calculationRunId": run["calculationRunId"]}

@router.post("/{project_id}/cash-flow/run")
def run_project_cash_flow(project_id: str, request: Request, input_data: Dict[str, Any] | None = None):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    project = _project_store()[project_id]
    reviewed_monthly = isinstance(input_data, dict) and "reviewedMonthlyFinancing" in input_data
    authorize_request(request, "assumption:override" if reviewed_monthly else "project:analyze")
    enforce_project_tenant_scope(request, project)
    try:
        request_data = build_cash_flow_request_use_case(project, input_data or {})
        if request_data.get("monthlyFinancing") is not None and not reviewed_monthly:
            authorize_request(request, "assumption:override")
            enforce_project_tenant_scope(request, project)
        result = execute_cash_flow_request_use_case(request_data)
    except FeasibilityReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    digest = financial_input_hash(request_data)
    run = _persist_calculation_run(project_id, engine_key="cash_flow_engine", result=result,
                                  input_data={**request_data, "financialInputHash": digest}, effective_input_captured=True)
    if request_data.get("monthlyFinancing") is not None:
        # Existing dependent engines use a different one-shot interest/claim
        # convention. Do not attach them to a monthly-debt scenario as if equal.
        record_sensitive_project_event(request, event_type="reviewed_monthly_cash_flow_calculated",
            project_id=project_id, metadata={"calculationRunId": run["calculationRunId"],
                "financialInputHash": digest, "method": request_data["monthlyFinancing"].get("method")})
        return {**result, "calculationRunId": run["calculationRunId"], "financialInputHash": digest,
            "dependentExecutionStatus": "partial",
            "dependentFailures": [{"engines": ["waterfall", "tranche_recovery", "unsold_ltv", "discount_stress"],
                "status": "not_supported", "reason": "reviewed_monthly_dependent_policy_required"}],
            "calculationRunIds": {"cash_flow_engine": run["calculationRunId"]}}
    scenario_input, stress_input = dependent_inputs(request_data, result, run["calculationRunId"])
    dependent_failures = []

    # A Cash Flow rerun is the financial starting point for the four review
    # panels shown on the Cash Flow screen.  Persist the dependent scenario
    # runs here so the UI never presents an apparently completed CF run with
    # empty waterfall, recovery, LTV, and discount-stress cards.
    scenario_run_ids: Dict[str, str] = {}
    discount_stress_run_id: str | None = None
    try:
        scenario_request = build_scenario_request_from_project({}, scenario_input)
        scenario_result = ScenarioEngineAgent().run_scenario_set(scenario_request)
        scenario_run_ids = _persist_scenario_calculation_runs(
            project_id,
            scenario_result.model_dump(),
            {**scenario_input, **scenario_request.model_dump(mode="json")},
            strict=True,
        )
    except Exception as exc:
        detail = exc.detail if isinstance(exc, HTTPException) and exc.status_code == 422 else None
        scenario_engines = ["waterfall", "tranche_recovery", "unsold_ltv"]
        if isinstance(detail, dict) and detail.get("error") == "insufficient_input":
            dependent_failures.append({
                "engines": scenario_engines,
                "status": "needs_input",
                "reason": "dependent_scenario_input_missing",
                "missingFields": detail.get("missingFields") or [],
            })
        else:
            dependent_failures.append({"engines": scenario_engines, "status": "failed", "reason": "dependent_scenario_failed"})
        scenario_run_ids = {}

    try:
        stress_request = build_stress_test_request_from_project({}, stress_input)
        stress_result = StressTestAgent().run_stress_test(stress_request)
        stress_payload = stress_test_run_payload(stress_result.model_dump())
        stress_run = _persist_calculation_run(
            project_id,
            engine_key=stress_payload["engineKey"],
            result=stress_payload["result"],
            input_data={**stress_input, **stress_request.model_dump(mode="json"), "engineVersion": stress_payload["engineVersion"]},
            artifact_ref=stress_payload["artifactRef"],
            effective_input_captured=True,
        )
        discount_stress_run_id = str(stress_run.get("calculationRunId"))
    except FinancialInputError as exc:
        if exc.code == "cash_flow_stress_period_unconfirmed":
            dependent_failures.append({"engines": ["discount_stress"], "status": "not_supported",
                                       "reason": "cash_flow_stress_period_unconfirmed", "message": "사건·일정의 시간 근거를 확인한 뒤 기간 스트레스를 실행해 주세요."})
        else:
            dependent_failures.append({"engines": ["discount_stress"], "status": "failed", "reason": "dependent_stress_failed"})
        discount_stress_run_id = None
    except Exception:
        dependent_failures.append({"engines": ["discount_stress"], "status": "failed", "reason": "dependent_stress_failed"})
        discount_stress_run_id = None

    return {
        **result,
        "calculationRunId": run["calculationRunId"],
        "financialInputHash": digest,
        "dependentExecutionStatus": "partial" if dependent_failures else "completed",
        "dependentFailures": dependent_failures,
        "calculationRunIds": {
            "cash_flow_engine": run["calculationRunId"],
            **scenario_run_ids,
            **({"discount_stress": discount_stress_run_id} if discount_stress_run_id else {}),
        },
    }

@router.post("/{project_id}/pf/structure")
def calculate_pf_structure(project_id: str, input_data: Dict[str, Any]):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    project = _project_store()[project_id]
    source_data = build_pf_structure_input(project, input_data)
    response = execute_pf_structure_input(source_data)
    captured_input = {**input_data, **source_data}
    pf_run = _persist_calculation_run(project_id, engine_key="pf_metrics", result=response,
                                    input_data=captured_input, effective_input_captured=True)
    ltv_run = _persist_calculation_run(
        project_id,
        engine_key="ltv_ltc_dscr",
        result=response,
        input_data={**captured_input, "engineVersion": "ltv_ltc_dscr_v1"},
        artifact_ref={"type": "pf_structure", "section": "metrics", "parentCalculationRunId": pf_run["calculationRunId"]},
        effective_input_captured=True,
    )
    exit_run = _persist_calculation_run(
        project_id,
        engine_key="exit_sale_rate",
        result=response,
        input_data={**captured_input, "engineVersion": "exit_sale_rate_v1"},
        artifact_ref={"type": "pf_structure", "section": "exit_sale_rate", "parentCalculationRunId": pf_run["calculationRunId"]},
        effective_input_captured=True,
    )
    return {
        **response,
        "calculationRunId": pf_run["calculationRunId"],
        "calculationRunIds": {
            "pf_metrics": pf_run["calculationRunId"],
            "ltv_ltc_dscr": ltv_run["calculationRunId"],
            "exit_sale_rate": exit_run["calculationRunId"],
        },
    }

@router.post("/{project_id}/returns/calculate")
def calculate_returns(project_id: str, input_data: Dict[str, Any] | None = None):
    """Project IRR, Equity IRR, Equity Multiple, Payback, and ICR.

    Reuses the project's feasibility inputs (for ICR's operating income and
    interest) and accepts an explicit ``project_cashflows`` / ``equity_cashflows``
    series or monthly ``revenue_schedule`` / ``cost_schedule`` overrides.
    """
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    feasibility = _project_store()[project_id].get("feasibility_input", {})
    source_data = deep_merge(feasibility, input_data or {})
    result = calculate_return_metrics(source_data)
    run = _persist_calculation_run(project_id, engine_key="return_metrics", result=result,
                                  input_data=source_data, effective_input_captured=True)
    return {**result, "calculationRunId": run["calculationRunId"]}

@router.post("/{project_id}/risk/assess")
def assess_risk(project_id: str, input_data: Dict[str, Any]):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    input_data = input_data or {}
    source_data = deep_merge(_project_store()[project_id], input_data.get("project_data", {}))
    raw_items = input_data.get("risk_items", source_data.get("risk_items", []))
    return build_basic_risk_matrix(source_data, raw_items)


def build_scenario_request_from_project(project: Dict[str, Any], input_data: Dict[str, Any]) -> ScenarioSetRequest:
    # 1. Product Mix
    raw_mix = (
        input_data.get("productMix")
        or input_data.get("product_mix")
        or project.get("productMix")
        or project.get("product_mix")
        or project.get("feasibility_input", {}).get("productMix")
        or project.get("feasibility_input", {}).get("product_mix")
        or []
    )
    product_mix = []
    for item in raw_mix:
        if isinstance(item, dict):
            product_mix.append({
                "type": item.get("type") or item.get("name") or "A",
                "units": int(item.get("units") or item.get("unit_count") or item.get("volume") or 0),
                "avgUnitPrice": float(item.get("avgUnitPrice") or item.get("avg_unit_price") or item.get("price") or 0.0),
                "totalRevenue": float(item.get("totalRevenue") or item.get("total_revenue") or item.get("revenue") or 0.0)
            })
    product_mix = [
        item for item in product_mix
        if item["units"] > 0 and item["totalRevenue"] > 0
    ]

    # 2. PF Structure
    raw_pf = (
        input_data.get("pfStructure")
        or input_data.get("pf_structure")
        or input_data.get("tranches")
        or project.get("pfStructure")
        or project.get("pf_structure")
        or project.get("tranches")
        or []
    )
    pf_structure = []
    for idx, item in enumerate(raw_pf):
        if isinstance(item, dict):
            pf_structure.append({
                "tranche": item.get("tranche") or item.get("name") or f"Tr.{idx+1}",
                "amount": float(item.get("amount") or item.get("principal") or 0.0),
                "priority": normalize_tranche_priority(item.get("priority"), idx + 1),
                "interestRate": float(item.get("interestRate") or item.get("interest_rate") or item.get("rate") or 0.0),
                "feeRate": float(item.get("feeRate") or item.get("fee_rate") or 0.0)
            })
    pf_structure = [item for item in pf_structure if item["amount"] > 0]

    missing_fields = []
    if not product_mix:
        missing_fields.append("productMix")
    if not pf_structure:
        missing_fields.append("pfStructure")
    if missing_fields:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "insufficient_input",
                "dataStatus": "insufficient_input",
                "missingFields": missing_fields,
                "message": (
                    "Scenario calculation requires source-backed product mix and PF tranche inputs. "
                    "No synthetic units, revenue, tranche amount, interest rate, or fee rate was generated."
                ),
                "evidenceEligible": False,
            },
        )

    # 3. Drivers
    drivers = input_data.get("scenarioDrivers") or {}

    # 4. Waterfall Rule
    rule = input_data.get("waterfallRule") or {}

    # 5. Metadata
    meta = input_data.get("sourceMetadata") or {
        "sourceName": "manual scenario input",
        "sourceType": "manual_upload"
    }

    return ScenarioSetRequest(
        scenarioSetId=input_data.get("scenarioSetId"),
        scenarioSetName=input_data.get("scenarioSetName"),
        baseDate=input_data.get("baseDate"),
        projectId=project.get("project", {}).get("id") or project.get("id"),
        productMix=product_mix,
        pfStructure=pf_structure,
        scenarioDrivers=drivers,
        waterfallRule=rule,
        sourceMetadata=meta
    )


def build_stress_test_request_from_project(project: Dict[str, Any], input_data: Dict[str, Any]) -> StressTestRequest:
    input_data = input_data or {}
    require_stress_calendar_period(input_data)
    feasibility = project.get("feasibility_input", {}) or {}

    def pick(*keys, default=0.0):
        for source in (input_data, feasibility):
            for key in keys:
                if source.get(key) not in (None, ""):
                    return _to_number(source.get(key))
        return default

    total_revenue = pick("totalRevenue", "total_revenue", "sales_revenue")
    land_cost = pick("landCost", "land_cost")
    construction_cost = pick("constructionCost", "construction_cost")
    finance_cost = pick("financeCost", "finance_cost")
    total_cost = pick("totalCost", "total_cost")
    other_cost = pick("otherCost", "other_cost")
    if other_cost <= 0 and total_cost > 0:
        other_cost = max(0.0, total_cost - land_cost - construction_cost - finance_cost)

    raw_tranches = input_data.get("pfStructure") or project.get("pf_structure", []) or []
    tranches: List[StressTranche] = []
    for index, tranche in enumerate(raw_tranches, start=1):
        if not isinstance(tranche, dict):
            continue
        priority = normalize_tranche_priority(tranche.get("priority"), index)
        tranches.append(StressTranche(
            tranche=str(tranche.get("tranche") or tranche.get("name") or f"Tr.{index}"),
            amount=_to_number(tranche.get("amount", 0.0)),
            priority=priority,
            interestRate=_to_number(tranche.get("interestRate", tranche.get("interest_rate", tranche.get("rate", 0.0)))),
            feeRate=_to_number(tranche.get("feeRate", tranche.get("fee_rate", 0.0))),
        ))

    # Freeze the same defaults the Agent consumes, so later policy/default
    # changes cannot reinterpret an old run's absent schedule or scenarios.
    scenarios = [scenario.model_copy(deep=True) for scenario in DEFAULT_STRESS_SCENARIOS]
    if isinstance(input_data.get("scenarios"), list) and input_data["scenarios"]:
        scenarios = [StressScenarioDriver(**s) for s in input_data["scenarios"]]
    schedule = StressTestSchedule(**input_data["schedule"]) if isinstance(input_data.get("schedule"), dict) else StressTestSchedule()

    return StressTestRequest(
        baseCashFlowRequest=input_data.get("baseCashFlowRequest"),
        projectId=input_data.get("projectId") or project.get("project", {}).get("id"),
        totalRevenue=total_revenue,
        landCost=land_cost,
        constructionCost=construction_cost,
        otherCost=other_cost,
        financeCost=finance_cost,
        pfAmount=_to_number(input_data.get("pfAmount", 0.0)),
        pfStructure=tranches,
        unsoldRecoveryHaircut=_to_number(input_data.get("unsoldRecoveryHaircut", 0.2)),
        hurdleRate=_to_number(input_data.get("hurdleRate", 0.12)),
        periodsPerYear=int(input_data.get("periodsPerYear", 12) or 12),
        schedule=schedule,
        scenarios=scenarios,
    )


def _to_number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


@router.post("/{project_id}/stress-test/run")
def run_project_stress_test(project_id: str, input_data: Dict[str, Any] | None = None):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    req = build_stress_test_request_from_project(_project_store()[project_id], input_data or {})
    result = StressTestAgent().run_stress_test(req)
    dump = result.model_dump()
    # Persist as a first-class CalculationRun (covers the discount-stress stages);
    # STRESS_SENIOR_SHORTFALL / UNSOLD_LTV_BREACH flags become auditable riskIssues.
    payload = stress_test_run_payload(dump)
    run = _persist_calculation_run(
        project_id,
        engine_key=payload["engineKey"],
        result=payload["result"],
        input_data={**(input_data or {}), **req.model_dump(mode="json"), "engineVersion": payload["engineVersion"]},
        artifact_ref=payload["artifactRef"],
        effective_input_captured=True,
    )
    dump["calculationRunId"] = run.get("calculationRunId")
    return dump


@router.post("/{project_id}/credit-enhancement/evaluate")
def evaluate_credit_enhancement(project_id: str, input_data: Dict[str, Any] | None = None):
    """Evaluate the six PF credit-enhancement measures and quantify their
    combined effect on senior LTV, credit grade, and DSCR."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    input_data = input_data or {}
    if input_data.get("baseSeniorLtv") in (None, ""):
        # Default the base senior LTV from the project's PF structure metrics.
        project = _project_store()[project_id]
        feasibility = project.get("feasibility_input", {})
        pf_source = {
            "tranches": project.get("pf_structure", []),
            "total_revenue": feasibility.get("total_revenue", 0),
            "total_cost": feasibility.get("total_cost", 0),
        }
        pf_metrics = calculate_pf_metrics(pf_source)
        input_data["baseSeniorLtv"] = pf_metrics["metrics"]["ltv"]["value"]
    req = CreditEnhancementRequest(**input_data)
    return CreditEnhancementAgent().evaluate(req).model_dump()


@router.post("/{project_id}/cash-sweep/simulate")
def simulate_project_cash_sweep(project_id: str, input_data: Dict[str, Any]):
    """Simulate monthly Cash Sweep + Reserve allocation against a tranche stack."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    req = CashSweepRequest(**(input_data or {}))
    return CashSweepAgent().simulate(req).model_dump()


@router.post("/{project_id}/scenarios/waterfall")
def run_project_scenarios_waterfall(project_id: str, input_data: Dict[str, Any]):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    req = build_scenario_request_from_project(_project_store()[project_id], input_data)
    agent = ScenarioEngineAgent()
    result = agent.run_scenario_set(req)
    run_id = _persist_scenario_calculation_runs(project_id, result.model_dump(),
        {**input_data, **req.model_dump(mode="json")}, engines=["waterfall"]).get("waterfall")
    return {
        "scenarioSetId": result.scenarioSetId,
        "waterfallScenarios": result.waterfallScenarios,
        "calculationRunId": run_id,
    }


@router.post("/{project_id}/scenarios/unsold-ltv-matrix")
def run_project_scenarios_unsold_ltv(project_id: str, input_data: Dict[str, Any]):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    req = build_scenario_request_from_project(_project_store()[project_id], input_data)
    agent = ScenarioEngineAgent()
    result = agent.run_scenario_set(req)
    run_id = _persist_scenario_calculation_runs(project_id, result.model_dump(),
        {**input_data, **req.model_dump(mode="json")}, engines=["unsold_ltv"]).get("unsold_ltv")
    return {
        "scenarioSetId": result.scenarioSetId,
        "unsoldLtvMatrix": result.unsoldLtvMatrix,
        "calculationRunId": run_id,
    }


@router.post("/{project_id}/scenarios/recovery")
def run_project_scenarios_recovery(project_id: str, input_data: Dict[str, Any]):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")

    project = _project_store()[project_id]
    req = build_scenario_request_from_project(project, input_data)
    agent = ScenarioEngineAgent()
    result = agent.run_scenario_set(req)

    # Persist in the project record
    project_copy = dict(project)
    project_copy.setdefault("scenario_results", {})
    dump = result.model_dump()
    project_copy["scenario_results"][result.scenarioSetId] = dump
    _project_store()[project_id] = project_copy
    persist_project(project_id)

    # Full scenario set = all three engines get their own CalculationRun so the
    # SENIOR_SHORTFALL / UNSOLD_LTV_BREACH history is queryable per engine.
    run_ids = _persist_scenario_calculation_runs(project_id, dump, {**input_data, **req.model_dump(mode="json")})
    dump["calculationRunIds"] = run_ids
    return dump

@router.post("/{project_id}/scenarios/restructuring")
def run_project_scenarios_restructuring(project_id: str, input_data: Dict[str, Any] | None = None):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")

    input_data = input_data or {}
    project = _project_store()[project_id]
    source_data = deep_merge(project, input_data.get("project_data", {}))
    req = build_scenario_request_from_project(source_data, input_data)
    scenario_result = ScenarioEngineAgent().run_scenario_set(req)
    return RestructuringAgent().build_restructuring_plan(
        req,
        scenario_result,
        input_data.get("options", {}),
    )

@router.post("/{project_id}/marketability/analyze")
def analyze_marketability(
    project_id: str,
    input_data: Dict[str, Any] | None = None,
    live_comparables: bool = False,
):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    source = _project_store()[project_id]
    if live_comparables and MolitRtmsConnector().configured():
        import copy

        source = copy.deepcopy(source)
        try:
            records, _diag = fetch_project_market_comparables(
                source, connector=MolitRtmsConnector(), deal_yms=recent_deal_yms(3)
            )
            inject_market_comparables(source, records)
        except Exception:
            pass  # live data must never break the on-demand analysis
    result = analyze_marketability_use_case(source, input_data)
    if (input_data or {}).get("persist"):
        project = dict(_project_store()[project_id])
        project["marketability"] = result
        project["marketabilityAnalysis"] = result
        if live_comparables and isinstance(source, dict) and source.get("market"):
            project["market"] = source.get("market")
        updated = update_project_use_case(project_repository(), project_id, project)
        if not updated:
            raise HTTPException(status_code=404, detail="Project not found")
    return result

@router.get("/{project_id}/land-evidence")
def list_project_land_evidence(project_id: str, request: Request, pnu: str | None = None, evidence_type: str | None = None):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "evidence:read")
    return {
        "schemaVersion": "realytics-land-evidence-list-v1",
        "projectId": project_id,
        "records": list_land_evidence_records(project_id, pnu=pnu, evidence_type=evidence_type),
    }

@router.post("/{project_id}/land-evidence")
def upsert_project_land_evidence(project_id: str, request: Request, input_data: Dict[str, Any]):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:write")
    raw_records = input_data.get("records") if isinstance(input_data.get("records"), list) else [input_data]
    records = [
        upsert_land_evidence_record(project_id, record)
        for record in raw_records
        if isinstance(record, dict)
    ]
    return {
        "schemaVersion": "realytics-land-evidence-upsert-v1",
        "projectId": project_id,
        "count": len(records),
        "records": records,
        "autoReanalysis": _maybe_auto_reanalyze(project_id, ["land_evidence"]),
    }

@router.get("/{project_id}/assumption-overrides")
def list_project_assumption_overrides(
    project_id: str,
    request: Request,
    status: str | None = None,
    check_key: str | None = None,
):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    return {
        "schemaVersion": "realytics-assumption-override-list-v1",
        "projectId": project_id,
        "records": list_assumption_override_records(project_id, status=status, check_key=check_key),
    }

@router.post("/{project_id}/assumption-overrides")
def upsert_project_assumption_override(project_id: str, request: Request, input_data: Dict[str, Any]):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authz = authorize_request(request, "assumption:override")
    raw_records = input_data.get("records") if isinstance(input_data.get("records"), list) else [input_data]
    try:
        records = [
            upsert_assumption_override_record(
                project_id,
                {
                    **record,
                    "approvedBy": record.get("approvedBy")
                    or record.get("approved_by")
                    or user_id_from_request(request),
                },
            )
            for record in raw_records
            if isinstance(record, dict)
        ]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    statuses = sorted({record.get("status") for record in records if record.get("status")})
    record_sensitive_project_event(
        request,
        event_type="assumption_override_approved" if statuses == ["active"] else "assumption_override_status_changed",
        project_id=project_id,
        metadata={
            "count": len(records),
            "overrideIds": [record.get("overrideId") for record in records],
            "checkKeys": sorted({record.get("checkKey") for record in records if record.get("checkKey")}),
            "statuses": statuses,
            "authorization": authz.get("authorization"),
        },
    )
    return {
        "schemaVersion": "realytics-assumption-override-upsert-v1",
        "projectId": project_id,
        "count": len(records),
        "records": records,
    }

def _require_restructuring_cure_authority() -> None:
    if storage_backend() in {"postgres", "postgresql"}:
        raise HTTPException(status_code=503, detail={
            "error": "restructuring_cure_authority_unavailable",
            "message": "증빙 보완 기록을 사용하려면 운영 서버의 보완 기록 저장소 연결이 필요합니다.",
        })


@router.get("/{project_id}/restructuring-cures")
def list_project_restructuring_cures(
    project_id: str,
    request: Request,
    status: str | None = None,
    cure_type: str | None = None,
):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    _require_restructuring_cure_authority()
    return {
        "schemaVersion": "realytics-restructuring-cure-list-v1",
        "projectId": project_id,
        "records": list_restructuring_cure_records(project_id, status=status, cure_type=cure_type),
    }

@router.post("/{project_id}/restructuring-cures")
def upsert_project_restructuring_cure(project_id: str, request: Request, input_data: Dict[str, Any]):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authz = authorize_request(request, "project:write")
    _require_restructuring_cure_authority()
    raw_records = input_data.get("records") if isinstance(input_data.get("records"), list) else [input_data]
    try:
        records = [
            upsert_restructuring_cure_record(
                project_id,
                {
                    **record,
                    "reviewedBy": record.get("reviewedBy")
                    or record.get("reviewed_by")
                    or user_id_from_request(request),
                },
            )
            for record in raw_records
            if isinstance(record, dict)
        ]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_sensitive_project_event(
        request,
        event_type="restructuring_cure_selected",
        project_id=project_id,
        metadata={
            "count": len(records),
            "cureIds": [record.get("cureId") for record in records],
            "cureTypes": sorted({record.get("cureType") for record in records if record.get("cureType")}),
            "statuses": sorted({record.get("status") for record in records if record.get("status")}),
            "authorization": authz.get("authorization"),
        },
    )
    return {
        "schemaVersion": "realytics-restructuring-cure-upsert-v1",
        "projectId": project_id,
        "count": len(records),
        "records": records,
    }

@router.get("/{project_id}/market-evidence")
def list_project_market_evidence(
    project_id: str,
    request: Request,
    run_id: str | None = None,
):
    project = authoritative_project(project_id, request)
    authorize_request(request, "evidence:read")
    enforce_project_tenant_scope(request, project)
    return list_market_evidence_use_case(market_evidence_repository(), project_id, run_id)

@router.post("/{project_id}/market-evidence/review")
def review_project_market_evidence(
    project_id: str,
    request: Request,
    input_data: Dict[str, Any],
):
    project = authoritative_project(project_id, request)
    authorization = authorize_request(request, "project:review")
    enforce_project_tenant_scope(request, project)
    review_input = dict(input_data or {})
    review_input["reviewed_by"] = verified_actor_id(authorization)
    review_input.pop("reviewedBy", None)

    try:
        return review_market_evidence_use_case(
            market_evidence_repository(),
            review_input,
            now_iso(),
            project_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.post("/{project_id}/legal/review")
def review_legal_and_permits(
    project_id: str,
    request: Request,
    input_data: Dict[str, Any] | None = None,
):
    project = _project_store().get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    authorization = authorize_request(request, "project:analyze")
    if not authorization.get("authenticated"):
        raise HTTPException(
            status_code=401,
            detail={
                "error": "authentication_required",
                "scope": "legal_review",
            },
        )
    enforce_project_tenant_scope(request, project)
    return review_legal_and_permits_use_case(project, input_data)


def _preview_land_legal(project_id: str, request: Request, response: Response, project: Dict[str, Any]) -> Dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    enforce_project_tenant_scope(request, project)
    options = _stored_chief_evidence_options(project_id, project, {})
    return run_legal_pod_use_case(project_id, project, options, persist=False)


@router.post("/{project_id}/legal-pod/preview")
def preview_project_land_legal(project_id: str, request: Request, response: Response):
    verified_actor_id(authorize_request(request, "project:analyze"))
    project = authoritative_project(project_id, request)
    result = _preview_land_legal(project_id, request, response, project)
    return {**result, "previewHash": compact_review_hash(compact_land_legal_review(result["legalReview"]))}


@router.post("/{project_id}/legal-pod/preview-summary")
def save_project_land_legal_summary(project_id: str, request: Request, response: Response, input_data: Dict[str, Any]):
    actor = verified_actor_id(authorize_request(request, "project:write"))
    repository = project_repository(request)
    project = repository.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    sections = input_data.get("sections")
    parcel_indexes = input_data.get("parcelIndexes") or []
    expected_hash = input_data.get("expectedPreviewHash")
    if (
        not isinstance(sections, list) or not 1 <= len(sections) <= 3
        or any(section not in ("permit", "height", "parcels") for section in sections)
        or not isinstance(parcel_indexes, list) or len(parcel_indexes) > 50
        or any(type(index) is not int or index <= 0 for index in parcel_indexes)
        or ("parcels" in sections) != bool(parcel_indexes)
        or not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
    ):
        raise HTTPException(status_code=400, detail="저장할 항목과 필지를 선택한 뒤 검토를 다시 확인하세요.")
    result = _preview_land_legal(project_id, request, response, project)
    compact = compact_land_legal_review(result["legalReview"])
    if expected_hash != compact_review_hash(compact):
        raise HTTPException(status_code=409, detail="검토 근거가 바뀌었습니다. 화면 검토를 다시 실행한 뒤 저장하세요.")
    selection = {section: compact[section] for section in sections}
    if parcel_indexes:
        selection["parcels"] = [parcel for parcel in compact["parcels"] if parcel.get("parcelIndex") in parcel_indexes]
        if {parcel["parcelIndex"] for parcel in selection["parcels"]} != set(parcel_indexes):
            raise HTTPException(status_code=400, detail="현재 검토에 있는 필지만 저장할 수 있습니다.")
    record_id = "land_legal_preview:" + compact_review_hash({"actor": actor, "selection": selection})
    existing = project.get("landLegalPreviewSummaries") or []
    previous = next((item for item in existing if isinstance(item, dict) and item.get("recordId") == record_id), None)
    if previous:
        return previous
    record = {
        "recordId": record_id,
        "analysisOnly": True,
        "reportEligible": False,
        "savedBy": actor,
        "updatedAt": now_iso(),
        "summary": selection,
    }
    # User notes stay outside the evidence, approval and report ledgers.
    summaries = [record, *existing]
    if len(json.dumps(summaries, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 32768:
        raise HTTPException(status_code=413, detail="프로젝트의 요약 보관 한도에 도달했습니다. 선택 항목을 줄여 주세요.")
    version = (project.get("_meta") or {}).get("stateVersion")
    try:
        update = {"landLegalPreviewSummaries": summaries}
        if storage_backend() in {"postgres", "postgresql"}:
            repository.update(project_id, update, expected_state_version=version)
        else:
            repository.update(project_id, update)
    except ProjectVersionConflict as exc:
        raise HTTPException(status_code=409, detail="프로젝트가 변경됐습니다. 미리보기를 다시 실행하세요.") from exc
    return record


@router.get("/{project_id}/legal-pod/preview-summary")
def list_project_land_legal_summaries(project_id: str, request: Request, response: Response):
    verified_actor_id(authorize_request(request, "project:read"))
    project = authoritative_project(project_id, request)
    enforce_project_tenant_scope(request, project)
    response.headers["Cache-Control"] = "private, no-store"
    return {"items": project.get("landLegalPreviewSummaries") or []}


@router.post("/{project_id}/legal-pod/run")
def run_legal_pod(project_id: str, request: Request, input_data: Dict[str, Any] | None = None):
    """Run the independent Legal Pod and persist its PF handoff/evidence ledger."""
    project = _project_store().get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    authorization = authorize_request(request, "project:analyze")
    if not authorization.get("authenticated"):
        raise HTTPException(
            status_code=401,
            detail={
                "error": "authentication_required",
                "scope": "legal_pod",
            },
        )
    enforce_project_tenant_scope(request, project)
    project_data = dict(project)
    result = run_legal_pod_use_case(project_id, project_data, _stored_chief_evidence_options(project_id, project_data, input_data or {}))
    user = authorization.get("user") or {}
    metadata = (
        project.get("_meta")
        if isinstance(project.get("_meta"), dict)
        else {}
    )
    tenant_id = str(
        user.get("tenantId")
        or metadata.get("tenantId")
        or metadata.get("tenant_id")
        or ""
    ).strip()
    actor_id = str(user.get("userId") or "").strip()
    snapshot_persistence: Dict[str, Any] = {
        "sealed": False,
        "reason": (
            "postgres_not_configured"
            if not postgres_configured()
            else "trusted_tenant_or_actor_missing"
        ),
    }
    if postgres_configured() and tenant_id and actor_id:
        try:
            overlay = PostgresLegalTenantOverlayRepository(
                tenant_id=tenant_id,
                project_id=project_id,
            )
            sealed = seal_legal_review_evidence_snapshot(
                result["legalReview"],
                repository=overlay,
                created_by=actor_id,
            )
            result["legalReview"] = sealed["legalReview"]
            if storage_backend() != "postgres":
                upsert_legal_review_record(project_id, result["legalReview"])
            snapshot_persistence = {
                key: value
                for key, value in sealed.items()
                if key != "legalReview"
            }
        except (TypeError, ValueError) as exc:
            # Contract/lineage failures stay visible but never promote the
            # review. Avoid returning SQL/provider details to the caller.
            snapshot_persistence = {
                "sealed": False,
                "reason": "verified_snapshot_contract_rejected",
                "errorType": type(exc).__name__,
            }
        except Exception as exc:
            snapshot_persistence = {
                "sealed": False,
                "reason": "verified_snapshot_store_unavailable",
                "errorType": type(exc).__name__,
            }
    projection_persistence: Dict[str, Any] = {
        "persisted": False,
        "reason": (
            "postgres_not_configured"
            if not postgres_configured()
            else "trusted_tenant_or_actor_missing"
        ),
    }
    if postgres_configured() and tenant_id and actor_id:
        try:
            projection_repository = (
                PostgresLegalReviewProjectionRepository(
                    tenant_id=tenant_id,
                    project_id=project_id,
                )
            )
            projection_persistence = persist_legal_pod_projections(
                result,
                repository=projection_repository,
                actor_id=actor_id,
            )
            result["normalizedProjectionPersistence"] = (
                projection_persistence
            )
            result["legalReview"]["normalizedProjectionPersisted"] = True
            result["permitReview"]["normalizedProjectionPersisted"] = True
        except (TypeError, ValueError) as exc:
            result = mark_projection_persistence_failure(
                result,
                reason="normalized_projection_contract_rejected",
                error_type=type(exc).__name__,
            )
            projection_persistence = result[
                "normalizedProjectionPersistence"
            ]
        except Exception as exc:
            result = mark_projection_persistence_failure(
                result,
                reason="normalized_projection_store_unavailable",
                error_type=type(exc).__name__,
            )
            projection_persistence = result[
                "normalizedProjectionPersistence"
            ]
    elif postgres_configured():
        result = mark_projection_persistence_failure(
            result,
            reason="trusted_tenant_or_actor_missing",
        )
        projection_persistence = result[
            "normalizedProjectionPersistence"
        ]
    else:
        result["normalizedProjectionPersistence"] = projection_persistence
    result["evidenceSnapshotPersistence"] = snapshot_persistence
    project_data["legalPermitReview"] = result["legalReview"]
    project_data["legalReview"] = result["legalReview"]
    project_data["permitReview"] = result["permitReview"]
    _project_store()[project_id].update({
        "legalPermitReview": result["legalReview"],
        "legalReview": result["legalReview"],
        "permitReview": result["permitReview"],
    })
    upsert_project_record(project_id, _project_store()[project_id])
    record_sensitive_project_event(
        request,
        event_type="legal_pod_run",
        project_id=project_id,
        metadata={
            "legalReviewId": result["legalReview"].get("reviewId"),
            "permitReviewId": result["permitReview"].get("reviewId"),
            "status": result["legalReview"].get("status"),
            "mcp": result["legalReview"].get("mcp"),
            "evidenceSnapshot": {
                "sealed": snapshot_persistence.get("sealed"),
                "reason": snapshot_persistence.get("reason"),
                "legalEvidenceSnapshotId": (
                    (snapshot_persistence.get("snapshot") or {}).get(
                        "legalEvidenceSnapshotId"
                    )
                    if isinstance(
                        snapshot_persistence.get("snapshot"),
                        dict,
                    )
                    else None
                ),
            },
            "normalizedProjection": {
                "persisted": projection_persistence.get("persisted"),
                "reason": projection_persistence.get("reason"),
                "legalReviewVersionId": projection_persistence.get(
                    "legalReviewVersionId"
                ),
                "permitReviewVersionId": projection_persistence.get(
                    "permitReviewVersionId"
                ),
            },
            "authorization": authorization.get("authorization"),
        },
    )
    return {"projectId": project_id, **result}


@router.get("/{project_id}/legal-reviews")
def list_project_legal_reviews(project_id: str, request: Request, limit: int = Query(20, ge=1, le=200)):
    project = _project_store().get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    authorization = authorize_request(request, "project:read")
    if not authorization.get("authenticated"):
        raise HTTPException(
            status_code=401,
            detail={"error": "authentication_required", "scope": "legal_review"},
        )
    enforce_project_tenant_scope(request, project)
    if postgres_configured():
        user = authorization.get("user") or {}
        metadata = (
            project.get("_meta")
            if isinstance(project.get("_meta"), dict)
            else {}
        )
        tenant_id = str(
            user.get("tenantId")
            or tenant_id_from_request(request)
            or metadata.get("tenantId")
            or metadata.get("tenant_id")
            or ""
        ).strip()
        if not tenant_id:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "normalized_legal_store_scope_missing",
                    "message": (
                        "Tenant scope is required for normalized legal "
                        "review reads."
                    ),
                },
            )
        try:
            repository = PostgresLegalReviewProjectionRepository(
                tenant_id=tenant_id,
                project_id=project_id,
            )
            items = repository.list_legal_reviews(limit)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "normalized_legal_store_unavailable",
                    "errorType": type(exc).__name__,
                },
            ) from exc
        return {
            "projectId": project_id,
            "items": items,
            "source": "normalized_postgres",
        }
    from storage import list_legal_review_records
    return {
        "projectId": project_id,
        "items": list_legal_review_records(project_id, limit),
        "source": "legacy_non_authoritative",
    }


@router.post("/{project_id}/legal-reviews/{review_id}/decision")
def decide_project_legal_review(
    project_id: str,
    review_id: str,
    request: Request,
    payload: Dict[str, Any],
):
    """Record an append-only, principal-bound legal human decision."""

    project = _project_store().get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    authorization = authorize_request(request, "legal:review")
    if not authorization.get("authenticated"):
        raise HTTPException(
            status_code=401,
            detail={
                "error": "authenticated_reviewer_required",
                "message": (
                    "Legal review decisions require an authenticated reviewer; "
                    "permissive pilot identity is not sufficient."
                ),
            },
        )
    if not is_explicit_human_principal(authorization):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "human_legal_reviewer_required",
                "message": (
                    "Legal review decisions require an explicitly classified "
                    "human principal; service identities cannot approve."
                ),
            },
        )
    enforce_project_tenant_scope(request, project)
    user = authorization.get("user") or {}
    reviewer_id = str(user.get("userId") or "").strip()
    app_role = str(authorization.get("role") or user.get("role") or "").lower()
    legal_role_by_app_role = {
        "legal_reviewer": "legal_reviewer",
        "legal_counsel_approver": "legal_counsel_approver",
        "external_counsel": "external_counsel",
        "compliance_officer": "compliance_officer",
    }
    reviewer_role = legal_role_by_app_role.get(app_role)
    if reviewer_role is None:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "legal_reviewer_role_required",
                "role": app_role,
            },
        )
    metadata = project.get("_meta") if isinstance(project.get("_meta"), dict) else {}
    tenant_id = str(user.get("tenantId") or metadata.get("tenantId") or "").strip()
    if (
        not postgres_configured()
        or not legal_review_database_configured()
        or not tenant_id
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "error": "normalized_legal_store_required",
                "message": (
                    "Legal review decisions require tenant-bound normalized "
                    "PostgreSQL and the dedicated legal-review DSN; runtime "
                    "and legacy projections are never approval authorities."
                ),
            },
        )
    normalized_repository = PostgresLegalTenantOverlayRepository(
        tenant_id=tenant_id,
        project_id=project_id,
        connection_factory=connect_legal_review_database,
    )
    projection_repository = PostgresLegalReviewProjectionRepository(
        tenant_id=tenant_id,
        project_id=project_id,
    )
    decision_writer = normalized_repository.append_review_decision
    try:
        result = decide_legal_review_use_case(
            project_id,
            review_id,
            payload,
            reviewer_id=reviewer_id,
            reviewer_role=reviewer_role,
            review_loader=(
                lambda requested_project_id, requested_limit: (
                    projection_repository.list_legal_reviews(
                        requested_limit
                    )
                    if requested_project_id == project_id
                    else []
                )
            ),
            decision_writer=decision_writer,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LegalReviewDecisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "legal_review_decision_predecessor_changed",
                "message": str(exc),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_sensitive_project_event(
        request,
        event_type="legal_review_decision",
        project_id=project_id,
        metadata={
            "legalReviewId": review_id,
            "decisionId": result["decision"].get("decisionId"),
            "decisionStatus": result["decision"].get("decisionStatus"),
            "reviewerRole": reviewer_role,
            "normalizedDecisionPersistence": result.get(
                "normalizedDecisionPersistence"
            ),
            "authorization": authorization.get("authorization"),
        },
    )
    return result


@router.get("/{project_id}/permit-reviews")
def list_project_permit_reviews(project_id: str, request: Request, limit: int = Query(20, ge=1, le=200)):
    project = _project_store().get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    authorization = authorize_request(request, "project:read")
    if not authorization.get("authenticated"):
        raise HTTPException(
            status_code=401,
            detail={"error": "authentication_required", "scope": "permit_review"},
        )
    enforce_project_tenant_scope(request, project)
    if postgres_configured():
        user = authorization.get("user") or {}
        metadata = (
            project.get("_meta")
            if isinstance(project.get("_meta"), dict)
            else {}
        )
        tenant_id = str(
            user.get("tenantId")
            or tenant_id_from_request(request)
            or metadata.get("tenantId")
            or metadata.get("tenant_id")
            or ""
        ).strip()
        if not tenant_id:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "normalized_legal_store_scope_missing",
                    "message": (
                        "Tenant scope is required for normalized permit "
                        "review reads."
                    ),
                },
            )
        try:
            repository = PostgresLegalReviewProjectionRepository(
                tenant_id=tenant_id,
                project_id=project_id,
            )
            items = repository.list_permit_reviews(limit)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "normalized_legal_store_unavailable",
                    "errorType": type(exc).__name__,
                },
            ) from exc
        return {
            "projectId": project_id,
            "items": items,
            "source": "normalized_postgres",
        }
    from storage import list_permit_review_records
    return {
        "projectId": project_id,
        "items": list_permit_review_records(project_id, limit),
        "source": "legacy_non_authoritative",
    }


@router.get("/{project_id}/legal-pod/law-changes")
def list_project_legal_law_changes(project_id: str, request: Request, limit: int = Query(100, ge=1, le=500)):
    """Return source-backed legal reference changes across Legal Pod runs."""
    project = _project_store().get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    authorization = authorize_request(request, "project:read")
    if not authorization.get("authenticated"):
        raise HTTPException(
            status_code=401,
            detail={"error": "authentication_required", "scope": "legal_change"},
        )
    enforce_project_tenant_scope(request, project)
    records = [
        record for record in list_deal_state_records(project_id)
        if record.get("recordType") == "legal_law_change"
    ]
    return {"projectId": project_id, "items": records[:limit]}

@router.post("/{project_id}/reports/generate")
def generate_report(project_id: str, request: Request, input_data: Dict[str, Any]):
    project = authoritative_project(project_id, request)
    authorization = authorize_request(request, "report:export")
    enforce_project_tenant_scope(request, project)
    cash_flow_run = None
    calculation_runs = {}
    if "cashFlowBasis" in input_data or "calculationBases" in input_data:
        # Only a reference and fingerprints cross this boundary, never client
        # supplied snapshots or financial overrides disguised as saved results.
        if set(input_data) - {"cashFlowBasis", "calculationBases", "templateId", "options"}:
            raise HTTPException(422, "저장 계산을 선택할 때 입력값이나 결과를 함께 덮어쓸 수 없습니다.")
        try:
            selections = ReportCalculationSelection.model_validate({"calculationBases": input_data["calculationBases"]}).calculationBases if "calculationBases" in input_data else {}
            if "cashFlowBasis" in input_data:
                selections["cash_flow"] = ReportCashFlowBasis.model_validate(input_data["cashFlowBasis"])
        except ValueError as exc:
            raise HTTPException(422, "보고서 계산 선택 정보가 올바르지 않습니다.") from exc
        tenant = str((project.get("_meta") or {}).get("tenantId")
                     or (project.get("_meta") or {}).get("tenant_id") or "legacy")
        if storage_backend() in {"postgres", "postgresql"} and tenant == "legacy":
            raise HTTPException(401, {"error": "tenant_required"})
        for slot, basis in selections.items():
            try:
                records = list_calculation_run_records(project_id, tenant_id=tenant,
                    calculation_run_id=basis.calculationRunId, limit=2)
            except (CalculationRunRepositoryError, ValueError) as exc:
                raise HTTPException(503, "계산 저장소를 조회하지 못했습니다. 다시 시도해 주세요.") from exc
            if len(records) != 1:
                raise HTTPException(404, "선택한 계산을 찾을 수 없습니다.")
            try:
                scope = {"project_id": project_id, "tenant_id": tenant, "selection": basis.model_dump()}
                if slot == "cash_flow":
                    cash_flow_run = freeze_cash_flow_basis(records[0], **scope)
                else:
                    calculation_runs[slot] = freeze_calculation_basis(records[0], slot=slot, **scope)
            except LookupError as exc:
                raise HTTPException(404, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
        input_data = {key: value for key, value in input_data.items() if key not in {"cashFlowBasis", "calculationBases"}}
    result = generate_and_save_pf_report_use_case(
        report_repository(request),
        project_id,
        project,
        input_data,
        cash_flow_run=cash_flow_run,
        calculation_runs=calculation_runs,
    )
    structured_report = result["generated"]["structuredReport"]
    md_report = result["generated"]["reportMarkdown"]
    report_record = result["reportRecord"]
    record_sensitive_project_event(
        request,
        event_type="pf_report_draft_generated",
        project_id=project_id,
        metadata={
            "reportId": report_record.get("report_id"),
            "version": report_record.get("version"),
            "status": report_record.get("status"),
            "reportEligible": (report_record.get("metadata") or {}).get("report_eligible"),
            "authorization": authorization.get("authorization"),
            "cashFlowCalculationRunId": cash_flow_run.get("calculationRunId") if cash_flow_run else None,
            "calculationRunIds": {slot: run["calculationRunId"] for slot, run in calculation_runs.items()},
        },
    )
    return {
        "project_id": project_id,
        "report_id": report_record["report_id"],
        "version": report_record["version"],
        "status": report_record["status"],
        "template": structured_report["template"],
        "report": structured_report,
        "report_markdown": md_report,
        "report_display_markdown": md_report,
        "paragraphEvidenceLinks": structured_report.get("paragraphEvidenceLinks"),
        "metadata": report_record["metadata"],
    }

@router.get("/{project_id}/reports")
def get_reports(project_id: str, request: Request):
    project = authoritative_project(project_id, request)
    authorize_request(request, "project:read")
    enforce_project_tenant_scope(request, project)
    return report_repository(request).list_metadata(project_id)

@router.get("/{project_id}/reports/calculation-bases")
def get_report_calculation_bases(project_id: str, request: Request):
    project = authoritative_project(project_id, request)
    authorize_request(request, "project:read")
    enforce_project_tenant_scope(request, project)
    tenant = str((project.get("_meta") or {}).get("tenantId")
                 or (project.get("_meta") or {}).get("tenant_id") or "legacy")
    if storage_backend() in {"postgres", "postgresql"} and tenant == "legacy":
        raise HTTPException(401, {"error": "tenant_required"})
    choices = []
    try:
        # Bound payload reads per engine; large result snapshots never cross
        # the selection endpoint. An exact ID is looked up again at creation.
        for slot, (_, engines) in CALCULATION_SLOTS.items():
            for engine in engines:
                records = list_calculation_run_records(project_id, tenant_id=tenant, engine_key=engine, limit=20)
                choices.extend(calculation_choice(run, slot) for run in records
                    if str(run.get("projectId")) == project_id and str(run.get("tenantId") or "legacy") == tenant)
    except (CalculationRunRepositoryError, ValueError) as exc:
        raise HTTPException(503, "계산 저장소를 조회하지 못했습니다. 다시 시도해 주세요.") from exc
    return {"records": choices, "perEngineLimit": 20}


@router.get("/{project_id}/reports/{report_id}")
def get_report(project_id: str, report_id: str, request: Request):
    project = authoritative_project(project_id, request)
    authorize_request(request, "project:read")
    enforce_project_tenant_scope(request, project)
    repo = report_repository(request)
    record = repo.get(project_id, report_id)
    if not record:
        raise HTTPException(status_code=404, detail="Report not found")
    return {
        **repo.metadata(record),
        "report": record.get("report"),
        "report_markdown": record.get("report_markdown"),
        "report_display_markdown": ReportExportAgent().export(record, "markdown")["content"],
        "exports": [repo.export_metadata(export) for export in record.get("exports", [])],
    }

@router.get("/{project_id}/report-templates")
def list_report_templates(project_id: str, request: Request):
    project = authoritative_project(project_id, request)
    authorize_request(request, "project:read")
    enforce_project_tenant_scope(request, project)
    try:
        return list_report_templates_use_case(project_repository(request), project_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.post("/{project_id}/report-templates")
def upsert_report_template(project_id: str, request: Request, input_data: Dict[str, Any]):
    project = authoritative_project(project_id, request)
    authz = authorize_request(request, "report:export")
    enforce_project_tenant_scope(request, project)
    try:
        result = upsert_report_template_use_case(
            project_repository(request),
            project_id,
            input_data,
            edited_by=input_data.get("editedBy") or input_data.get("edited_by") or user_id_from_request(request),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    record_sensitive_project_event(
        request,
        event_type="report_template_upserted",
        project_id=project_id,
        metadata={
            "templateId": result.get("template", {}).get("templateId"),
            "reportType": result.get("template", {}).get("reportType"),
            "version": result.get("template", {}).get("version"),
            "authorization": authz.get("authorization"),
        },
    )
    return result

@router.get("/{project_id}/report-templates/{template_id}")
def get_report_template(project_id: str, template_id: str, request: Request):
    project = authoritative_project(project_id, request)
    authorize_request(request, "project:read")
    enforce_project_tenant_scope(request, project)
    try:
        return get_report_template_use_case(
            project_repository(request),
            project_id,
            template_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.post("/{project_id}/reports/{report_id}/exports")
def create_report_export(project_id: str, report_id: str, request: Request, input_data: Dict[str, Any] | None = None):
    project = authoritative_project(project_id, request)
    authz = authorize_request(request, "report:export")
    enforce_project_tenant_scope(request, project)
    try:
        export_record = export_saved_report_use_case(
            report_repository(request),
            project_id,
            report_id,
            input_data,
        )
    except ReportPublicationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_sensitive_project_event(
        request,
        event_type="report_export_created",
        project_id=project_id,
        metadata={
            "reportId": report_id,
            "exportId": export_record.get("export_id"),
            "format": export_record.get("format"),
            "fileName": export_record.get("file_name"),
            "contentType": export_record.get("content_type"),
            "authorization": authz.get("authorization"),
        },
    )
    return report_repository(request).export_metadata(export_record)


@router.post("/{project_id}/reports/{report_id}/publish")
def publish_report(project_id: str, report_id: str, request: Request):
    project = authoritative_project(project_id, request)
    authorization = authorize_request(request, "project:review")
    enforce_project_tenant_scope(request, project)
    enforce_high_risk_step_up(
        authorization,
        action="pf_report_final_publish",
    )
    actor_id = user_id_from_request(request)
    try:
        record = publish_saved_report_use_case(
            report_repository(request),
            orchestration_repository(request),
            project_snapshot_repository(request),
            project_id,
            report_id,
            published_by=actor_id,
            published_at=now_iso(),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    record_sensitive_project_event(
        request,
        event_type="pf_report_final_published",
        project_id=project_id,
        run_id=record.get("run_id"),
        metadata={
            "reportId": report_id,
            "contentSha256": (record.get("metadata") or {}).get("content_sha256"),
            "projectSnapshotId": (
                (record.get("publication") or {}).get("decisionPackage") or {}
            ).get("projectSnapshotId"),
            "authorization": authorization.get("authorization"),
            "identityAssurance": authorization.get("identityAssurance"),
        },
    )
    return report_repository(request).metadata(record)

@router.get("/{project_id}/reports/{report_id}/exports")
def list_report_exports(project_id: str, report_id: str, request: Request):
    project = authoritative_project(project_id, request)
    authorize_request(request, "project:read")
    enforce_project_tenant_scope(request, project)
    repo = report_repository(request)
    record = repo.get(project_id, report_id)
    if not record:
        raise HTTPException(status_code=404, detail="Report not found")
    return [repo.export_metadata(export) for export in record.get("exports", [])]

@router.get("/{project_id}/reports/{report_id}/exports/{export_id}/download")
def download_report_export(project_id: str, report_id: str, export_id: str, request: Request):
    project = authoritative_project(project_id, request)
    authz = authorize_request(request, "report:export")
    enforce_project_tenant_scope(request, project)
    repo = report_repository(request)
    record = repo.get(project_id, report_id)
    if not record:
        raise HTTPException(status_code=404, detail="Report not found")
    export_record = repo.get_export(record, export_id)
    if not export_record:
        raise HTTPException(status_code=404, detail="Report export not found")
    content = export_record.get("content", "")
    if export_record.get("content_encoding") == "base64":
        content = base64.b64decode(content)
    record_sensitive_project_event(
        request,
        event_type="report_export_download",
        project_id=project_id,
        metadata={
            "reportId": report_id,
            "exportId": export_id,
            "format": export_record.get("format"),
            "fileName": export_record.get("file_name"),
            "contentType": export_record.get("content_type"),
            "authorization": authz.get("authorization"),
        },
    )
    return Response(
        content=content,
        media_type=export_record.get("content_type", "text/plain; charset=utf-8"),
        headers={
            "Content-Disposition": f'attachment; filename="{export_record.get("file_name", "report-export.txt")}"'
        },
    )

def _hydrated_project_source(
    project_id: str,
    input_data: Dict[str, Any],
    authoritative_source: Dict[str, Any] | None = None,
    *,
    include_active_cures: bool = True,
) -> Dict[str, Any]:
    base = authoritative_source or _project_store().get(project_id)
    if base is None:
        raise LookupError("Project not found")
    source_data = deep_merge(base, (input_data or {}).get("project_data", {}))
    source_data = attach_persisted_land_evidence(project_id, source_data)
    source_data = attach_persisted_parsed_facts(project_id, source_data)
    source_data = attach_approved_assumption_overrides(project_id, source_data)
    if include_active_cures:
        source_data = attach_active_restructuring_cures(project_id, source_data)
    return source_data


def _project_display_name(project_id: str, project_data: Dict[str, Any]) -> str:
    project = project_data.get("project") if isinstance(project_data.get("project"), dict) else {}
    stored_name = (
        project.get("name")
        or project.get("projectName")
        or project_data.get("projectName")
    )
    if stored_name and not _looks_like_corrupted_korean(stored_name):
        return stored_name
    inferred_name = _infer_project_name_from_source_documents(project_data)
    return inferred_name or stored_name or f"프로젝트 {project_id}"


def _resume_action(
    project: Dict[str, Any],
    documents: list[Dict[str, Any]],
    latest_run: Dict[str, Any],
    gate: Dict[str, Any],
) -> str:
    if not documents:
        return "upload_source_documents"
    if not latest_run:
        return "run_initial_analysis"
    if str(gate.get("status") or "").lower() in {"not_ready_for_approval", "needs_review", "hold"}:
        return "resume_open_reviews"
    if str(latest_run.get("status") or "").lower() in {"running", "queued", "failed"}:
        return "inspect_latest_run"
    return "continue_next_review_step"


def _recent_work_kind(
    latest_run: Dict[str, Any],
    approval_status: str,
) -> str:
    """Classify one resumable item without conflating execution and approval."""
    if not latest_run:
        return "project"
    if approval_status in {"pending", "approved", "rejected", "superseded"}:
        return "review"
    return "analysis"


def _recent_work_updated_at(
    project: Dict[str, Any],
    latest_run: Dict[str, Any],
) -> str | None:
    metadata = project.get("_meta") if isinstance(project.get("_meta"), dict) else {}
    value = (
        latest_run.get("completed_at")
        or latest_run.get("started_at")
        or metadata.get("updatedAt")
        or metadata.get("updated_at")
        or project.get("updatedAt")
        or project.get("updated_at")
        or metadata.get("createdAt")
        or metadata.get("created_at")
    )
    normalized = str(value or "").strip()
    return normalized or None


def _recent_work_href(project_id: str, next_action: str) -> str:
    encoded_project_id = quote(str(project_id), safe="")
    base = f"/project/{encoded_project_id}"
    if next_action == "upload_source_documents":
        return f"{base}/input-package"
    if next_action in {
        "run_initial_analysis",
        "resume_open_reviews",
        "inspect_latest_run",
        "continue_next_review_step",
    }:
        return f"{base}/chief-orchestration"
    return base


def _recent_work_summary(
    *,
    work_kind: str,
    execution_status: str,
    approval_status: str,
    document_count: int,
    open_review_count: int,
    risk_count: int,
) -> str:
    """Return only persisted counts and status axes; never synthesize a verdict."""
    facts = [f"원본 자료 {document_count}건"]
    if work_kind != "project":
        facts.append(f"실행 {execution_status}")
    if approval_status != "not_requested":
        facts.append(f"승인 {approval_status}")
    if open_review_count:
        facts.append(f"미해결 검토 {open_review_count}건")
    if risk_count:
        facts.append(f"등록 리스크 {risk_count}건")
    return " · ".join(facts)


def _looks_like_corrupted_korean(value: Any) -> bool:
    text = str(value or "")
    if not text:
        return False
    hangul_count = sum(1 for char in text if "\uac00" <= char <= "\ud7a3")
    cjk_count = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    replacement_count = text.count("�") + text.count("?")
    return hangul_count == 0 and (cjk_count >= 3 or replacement_count >= 3)


def _infer_project_name_from_source_documents(project_data: Dict[str, Any]) -> str | None:
    documents = list(project_data.get("source_documents") or [])
    upload_meta = (project_data.get("_meta") or {}).get("upload")
    if isinstance(upload_meta, dict):
        documents.append(upload_meta)
    for document in documents:
        if not isinstance(document, dict):
            continue
        filename = str(document.get("originalFileName") or document.get("fileName") or "")
        if not any("\uac00" <= char <= "\ud7a3" for char in filename):
            continue
        parenthesized = re.findall(r"\(([^()]{4,80})\)", filename)
        for candidate in parenthesized:
            if any("\uac00" <= char <= "\ud7a3" for char in candidate):
                return candidate.strip()
        stem = re.sub(r"\.[^.]+$", "", filename).strip()
        stem = re.sub(r"^\d{6,8}[-_\s]*", "", stem)
        stem = re.sub(r"^(사업계획서|사업계획|검토보고서|토지조서|토지대장)[-_()\s]*", "", stem)
        if len(stem) >= 4:
            return stem[:80].strip()
    return None


def _summarize_source_documents(documents: list[Dict[str, Any]]) -> Dict[str, Any]:
    by_type: Dict[str, int] = {}
    for document in documents:
        doc_type = document.get("documentType") or document.get("document_type") or "source_document"
        by_type[doc_type] = by_type.get(doc_type, 0) + 1
    return {
        "count": len(documents),
        "byType": by_type,
        "latestUploadedAt": max(
            [str(document.get("uploadedAt") or document.get("uploaded_at") or "") for document in documents],
            default=None,
        ) or None,
    }


def _summarize_land_ledger(project_data: Dict[str, Any]) -> Dict[str, Any]:
    ledger = project_data.get("landLedger") or project_data.get("land_ledger") or {}
    parcels = ledger.get("parcels") if isinstance(ledger, dict) else []
    parcels = parcels if isinstance(parcels, list) else []
    pnu_count = sum(1 for parcel in parcels if isinstance(parcel, dict) and parcel_pnu(parcel))
    return {
        "present": bool(parcels),
        "parcelCount": len(parcels),
        "pnuCount": pnu_count,
        "needsPnuEnrichment": bool(parcels) and pnu_count < len(parcels),
        "status": ledger.get("status") if isinstance(ledger, dict) else None,
    }


def _summarize_land_evidence(records: list[Dict[str, Any]]) -> Dict[str, Any]:
    by_type: Dict[str, int] = {}
    for record in records:
        evidence_type = record.get("evidenceType") or record.get("evidence_type") or "land_evidence"
        by_type[evidence_type] = by_type.get(evidence_type, 0) + 1
    return {
        "count": len(records),
        "byType": by_type,
        "hasOfficialLandPrice": by_type.get("official_land_price", 0) > 0,
        "hasRegistryEvidence": by_type.get("registry_record", 0) > 0,
        "hasPnuResolution": by_type.get("pnu_resolution", 0) > 0,
    }


def _summarize_chief_artifacts(records: list[Dict[str, Any]]) -> Dict[str, Any]:
    by_pod: Dict[str, int] = {}
    latest_by_pod: Dict[str, Dict[str, Any]] = {}
    for record in records:
        pod_key = record.get("podKey") or record.get("pod_key") or "unknown"
        by_pod[pod_key] = by_pod.get(pod_key, 0) + 1
        if pod_key not in latest_by_pod:
            latest_by_pod[pod_key] = {
                "artifactKey": record.get("artifactKey"),
                "artifactType": (record.get("result") or {}).get("artifactType"),
                "createdAt": record.get("createdAt"),
            }
    return {
        "count": len(records),
        "byPod": by_pod,
        "latestByPod": latest_by_pod,
    }


def _persist_shared_deal_state_graph(project_id: str, plan: Dict[str, Any], *, source: str = "workspace_context") -> Dict[str, Any]:
    project_data = _project_store().get(project_id)
    if not project_data:
        return {}
    graph = plan.get("sharedDealStateGraph") or {}
    task_graph = plan.get("taskGraph") or {}
    record = {
        "schemaVersion": "realytics-shared-deal-state-graph-record-v1",
        "projectId": project_id,
        "projectName": _project_display_name(project_id, project_data),
        "workflowTrack": task_graph.get("workflowTrack"),
        "analysisPurpose": plan.get("analysisPurpose"),
        "updatedAt": now_iso(),
        "updatedBy": source,
        "graph": graph,
        "summary": {
            "evidenceCount": len(graph.get("evidenceVault") or []),
            "assumptionCount": len(graph.get("assumptionBook") or []),
            "metricCount": len(graph.get("metrics") or []),
            "missingInputCount": len(graph.get("missingInputRegister") or []),
            "podCount": len(plan.get("agentPods") or []),
            "decision": (plan.get("decisionGate") or {}).get("decision"),
        },
    }
    state = project_data.setdefault("chief_orchestration_state", {})
    state["sharedDealStateGraph"] = record
    project_data["sharedDealStateGraph"] = record
    persist_project(project_id)
    return record


def _latest_pod_artifact_key_from_plan(plan: Dict[str, Any], pod_key: str) -> str | None:
    for run in plan.get("podRuns") or []:
        if run.get("podKey") != pod_key:
            continue
        artifact_keys = run.get("artifactKeys") or []
        if artifact_keys:
            return str(artifact_keys[-1])
    return None


def _completed_pod_artifact_keys_from_plan(plan: Dict[str, Any]) -> Dict[str, str]:
    artifact_keys: Dict[str, str] = {}
    for run in plan.get("podRuns") or []:
        pod_key = run.get("podKey")
        if not pod_key or run.get("status") not in {"completed", "needs_recalculation"}:
            continue
        keys = run.get("artifactKeys") or []
        if keys:
            artifact_keys[str(pod_key)] = str(keys[-1])
    return artifact_keys


def _validate_supplement_and_revalidate_parsed_facts(
    project_id: str,
    pod_key: str,
    *,
    project_data: Dict[str, Any],
    source_artifact_key: str | None,
    actor: str,
) -> Dict[str, Any]:
    initial = validate_parsed_facts_for_pod(
        project_id,
        pod_key,
        source_artifact_key=source_artifact_key,
        actor=actor,
    )
    supplement = auto_supplement_validation_gaps(
        project_id,
        pod_key,
        initial,
        project_data,
        actor=actor,
    )
    if int((supplement.get("summary") or {}).get("createdRecordCount") or 0) <= 0:
        return {
            **initial,
            "autoSupplement": supplement,
        }
    final = validate_parsed_facts_for_pod(
        project_id,
        pod_key,
        source_artifact_key=source_artifact_key,
        actor=actor,
    )
    return {
        **final,
        "initialValidation": initial,
        "autoSupplement": supplement,
    }


@router.post("/{project_id}/chief-orchestration/unsold-ltv-cure")
def apply_unsold_ltv_cure_and_rerun(project_id: str, request: Request, input_data: Dict[str, Any]):
    """Persist a source-backed Unsold LTV cure, rerun PF review, and refresh Gate v2."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    # A cure is a lender-side risk review action, not an edit to the project's
    # master data. Reviewers may submit it with risk:review; project:write
    # remains reserved for changing the canonical project input package.
    authz = authorize_request(request, "risk:review")
    _require_restructuring_cure_authority()
    payload = input_data or {}
    cure_type = str(payload.get("cureType") or payload.get("cure_type") or "").strip()
    amount = float(payload.get("amount") or 0)
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    evidence_id = str(
        evidence.get("evidenceId")
        or evidence.get("sourceUrlOrEvidenceId")
        or evidence.get("source_url_or_evidence_id")
        or ""
    ).strip()
    if not cure_type:
        raise HTTPException(status_code=400, detail="cureType is required")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be greater than zero")
    if not evidence_id:
        raise HTTPException(status_code=400, detail="Source-backed evidenceId or sourceUrlOrEvidenceId is required")

    actor = str(identity_context_from_request(request).get("userId") or "chief_deal_orchestrator")
    cure_record = {
        "cureId": str(payload.get("cureId") or f"cure-{project_id}-unsold-ltv-{cure_type}-{int(amount)}"),
        "cureType": cure_type,
        "status": "active",
        "amount": amount,
        "riskCodes": ["UNSOLD_LTV_BREACH"],
        "reviewedBy": actor,
        "description": str(payload.get("description") or "Source-backed Unsold LTV cure selected in Chief Deal Room."),
        "evidenceIds": [evidence_id],
        "sourceUrlOrEvidenceId": evidence_id,
        "sourceLocators": evidence.get("sourceLocators") or evidence.get("sourceLocator") or [],
        "sourceName": evidence.get("sourceName"),
        "sourceType": evidence.get("sourceType"),
        "note": evidence.get("note"),
    }
    try:
        saved_cure = upsert_restructuring_cure_record(project_id, cure_record)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    project_data = attach_approved_assumption_overrides(project_id, _project_store()[project_id])
    project_data = attach_active_restructuring_cures(project_id, project_data)
    project_data = dict(project_data)
    project_data["adoptedRestructuringCures"] = [saved_cure]
    rerun = run_and_save_pf_review_use_case(
        orchestration_repository(request),
        project_id,
        project_data,
        {"trigger": "unsold_ltv_cure", "cureId": saved_cure.get("cureId")},
    )
    _project_store()[project_id] = project_data
    record_sensitive_project_event(
        request,
        event_type="unsold_ltv_cure_rerun_completed",
        project_id=project_id,
        metadata={
            "cureId": saved_cure.get("cureId"),
            "cureType": cure_type,
            "amount": amount,
            "evidenceId": evidence_id,
            "authorization": authz.get("authorization"),
        },
    )
    artifacts = rerun.get("artifacts") or {}
    rerun_response = {**rerun, "projectId": project_id}
    return {
        "schemaVersion": "realytics-unsold-ltv-cure-rerun-v1",
        "projectId": project_id,
        "cure": saved_cure,
        "calculationRunIds": rerun.get("calculationRunIds") or {},
        "restructuringPlan": artifacts.get("restructuringPlan"),
        "approvalReadinessGate": artifacts.get("approvalReadinessGate"),
        "run": rerun_response,
    }


def _normalize_workspace_page(page: str) -> str:
    normalized = (page or "overview").replace("-", "_")
    aliases = {
        "input": "input_package",
        "intake": "input_package",
        "input_package": "input_package",
        "land_review": "land",
        "land_legal": "land",
        "map": "map",
        "map_3d": "map",
        "market": "marketability",
        "market_product": "marketability",
        "cash_flow": "cashflow",
        "finance_structure": "pf",
        "pf_credit": "pf",
        "pf_structure": "pf",
        "risk": "risk",
        "risk_register": "risk",
        "evidence_graph": "evidence",
    }
    return aliases.get(normalized, normalized)


WORKSPACE_PAGE_DATA_CONTRACTS: Dict[str, Dict[str, Any]] = {
    "overview": {
        "contractVersion": "realytics-workspace-page-data-contract-v1",
        "page": "overview",
        "sourceOfTruth": ["projects", "chief_orchestration_state", "chief_pod_artifacts"],
        "requiredPageDataFields": ["sourceDocumentSummary", "landLedgerSummary", "landEvidenceSummary", "chiefArtifactSummary", "agentPods", "podRuns", "taskGraph"],
    },
    "input_package": {
        "contractVersion": "realytics-workspace-page-data-contract-v1",
        "page": "input_package",
        "sourceOfTruth": ["source_documents", "projects.landLedger", "land_evidence", "assumption_overrides", "chief_orchestration_state"],
        "requiredPageDataFields": ["sourceDocumentSummary", "sourceDocuments", "landLedgerSummary", "landEvidenceSummary", "landEvidenceRecords", "assumptionOverrides", "missingInputRegister", "recommendedActions"],
    },
    "land": {
        "contractVersion": "realytics-workspace-page-data-contract-v1",
        "page": "land",
        "sourceOfTruth": ["projects.land", "projects.landLedger", "land_evidence", "chief_pod_artifacts.land_legal"],
        "requiredPageDataFields": ["land", "landLedger", "landLedgerSummary", "landEvidenceSummary", "landEvidenceRecords", "pnuValidation"],
    },
    "map": {
        "contractVersion": "realytics-workspace-page-data-contract-v1",
        "page": "map",
        "sourceOfTruth": ["projects.land", "projects.landLedger", "land_evidence", "map_intelligence"],
        "requiredPageDataFields": ["land", "landLedger", "landLedgerSummary", "landEvidenceSummary", "landEvidenceRecords", "mapDataEndpoint"],
    },
    "marketability": {
        "contractVersion": "realytics-workspace-page-data-contract-v1",
        "page": "marketability",
        "sourceOfTruth": ["projects.market", "projects.marketability", "projects.marketEvidence", "chief_pod_artifacts.market_product"],
        "requiredPageDataFields": ["market", "marketability", "marketEvidence", "sourceDocumentSummary", "chiefArtifactSummary"],
    },
    "feasibility": {
        "contractVersion": "realytics-workspace-page-data-contract-v1",
        "page": "feasibility",
        "sourceOfTruth": ["projects.feasibility", "projects.developmentFeasibility", "projects.productMix", "chief_pod_artifacts.feasibility_cashflow"],
        "requiredPageDataFields": ["feasibility", "developmentFeasibility", "cashFlow", "productMix", "chiefArtifactSummary"],
    },
    "cashflow": {
        "contractVersion": "realytics-workspace-page-data-contract-v1",
        "page": "cashflow",
        "sourceOfTruth": ["projects.cashFlow", "projects.cashFlowRunResult", "chief_pod_artifacts.feasibility_cashflow"],
        "requiredPageDataFields": ["feasibility", "developmentFeasibility", "cashFlow", "productMix", "chiefArtifactSummary"],
    },
    "pf": {
        "contractVersion": "realytics-workspace-page-data-contract-v1",
        "page": "pf",
        "sourceOfTruth": ["projects.pfStructure", "projects.riskRegister", "projects.creditReviewDecision", "chief_pod_artifacts.pf_credit"],
        "requiredPageDataFields": ["pfStructure", "riskRegister", "creditReviewDecision", "latestPfCreditArtifact", "chiefArtifacts"],
    },
    "risk": {
        "contractVersion": "realytics-workspace-page-data-contract-v1",
        "page": "risk",
        "sourceOfTruth": ["projects.riskRegister", "projects.creditReviewDecision", "chief_pod_artifacts.pf_credit", "evidence_graphs"],
        "requiredPageDataFields": ["riskRegister", "creditReviewDecision", "latestPfCreditArtifact", "chiefArtifacts"],
    },
    "evidence": {
        "contractVersion": "realytics-workspace-page-data-contract-v1",
        "page": "evidence",
        "sourceOfTruth": ["evidence_graphs", "source_documents", "land_evidence", "chief_pod_artifacts"],
        "requiredPageDataFields": ["sourceDocumentSummary", "landEvidenceSummary", "chiefArtifactSummary", "evidenceGraphEndpoint", "sourceDocuments", "landEvidenceRecords"],
    },
}


def _workspace_page_data_contract(page: str) -> Dict[str, Any]:
    normalized = _normalize_workspace_page(page)
    return WORKSPACE_PAGE_DATA_CONTRACTS.get(normalized, WORKSPACE_PAGE_DATA_CONTRACTS["overview"])


def _latest_chief_artifact_for_pod(chief_artifacts: list[Dict[str, Any]], pod_key: str) -> Dict[str, Any]:
    for record in chief_artifacts:
        if record.get("podKey") == pod_key:
            result = record.get("result") if isinstance(record.get("result"), dict) else {}
            return {
                "artifactKey": record.get("artifactKey"),
                "runId": record.get("runId"),
                "podKey": record.get("podKey"),
                "artifactType": result.get("artifactType") or record.get("artifactType"),
                "createdAt": record.get("createdAt"),
                "updatedAt": record.get("updatedAt"),
            }
    return {}


def _workspace_page_data(
    *,
    project_id: str,
    page: str,
    source_data: Dict[str, Any],
    plan: Dict[str, Any] | None,
    source_documents: list[Dict[str, Any]],
    land_evidence_records: list[Dict[str, Any]] | None,
    parsed_fact_package_data: Dict[str, Any],
    assumption_overrides: list[Dict[str, Any]] | None,
    chief_artifacts: list[Dict[str, Any]],
) -> Dict[str, Any]:
    normalized_page = _normalize_workspace_page(page)
    land_ledger = source_data.get("landLedger") or source_data.get("land_ledger")
    common = {
        "sourceDocumentSummary": _summarize_source_documents(source_documents),
        "parsedFactSummary": parsed_fact_package_data.get("summary") or {},
        "parsedFactCount": parsed_fact_package_data.get("recordCount") or 0,
        "chiefArtifactSummary": _summarize_chief_artifacts(chief_artifacts),
    }
    if plan is not None or (isinstance(land_ledger, dict) and isinstance(land_ledger.get("parcels"), list)):
        common["landLedgerSummary"] = _summarize_land_ledger(source_data)
    if land_evidence_records is not None:
        common["landEvidenceSummary"] = _summarize_land_evidence(land_evidence_records)
    if assumption_overrides is not None:
        common["assumptionOverrideCount"] = len(assumption_overrides)

    if normalized_page == "input_package":
        data = {
            **common,
            "sourceDocuments": source_documents,
            "parsedFacts": parsed_fact_package_data.get("records") or [],
            "recommendedActions": [
                "업로드 문서를 자동분류한 뒤 프로젝트 DB에 원본과 메타데이터를 저장합니다.",
                "토지조서가 있으면 PNU, 공시지가, 등기/제한물권 증빙 보강을 먼저 실행합니다.",
                "시장·분양성 근거와 사업수지/CF가 준비된 뒤 PF-Credit Pod를 실행합니다.",
            ],
        }
        if plan is not None:
            data["missingInputRegister"] = (plan.get("sharedDealStateGraph") or {}).get("missingInputRegister") or []
        if land_evidence_records is not None:
            data["landEvidenceRecords"] = land_evidence_records
        if assumption_overrides is not None:
            data["assumptionOverrides"] = assumption_overrides
        return data

    if normalized_page in {"land", "map"}:
        data = {
            **common,
            "land": source_data.get("land") or {},
            "landLedger": land_ledger or {},
            "mapDataEndpoint": f"/projects/{project_id}/map?resolve_geometry=true&geometry_limit=150",
        }
        if land_evidence_records is not None:
            data["landCostReconciliation"] = reconcile_land_costs_use_case(source_data)
            data["landEvidenceRecords"] = land_evidence_records
        if "landLedgerSummary" in common:
            data["pnuValidation"] = {
                "required": True,
                "ready": not common["landLedgerSummary"]["needsPnuEnrichment"],
                "pnuCount": common["landLedgerSummary"]["pnuCount"],
                "parcelCount": common["landLedgerSummary"]["parcelCount"],
            }
        return data

    if normalized_page == "marketability":
        return {
            **common,
            "market": source_data.get("market") or {},
            "marketability": source_data.get("marketability") or source_data.get("marketabilityAnalysis") or {},
            "marketEvidence": source_data.get("marketEvidence") or source_data.get("market_evidence") or {},
        }

    if normalized_page in {"feasibility", "cashflow"}:
        return {
            **common,
            "feasibility": source_data.get("feasibility") or source_data.get("feasibilityInput") or {},
            "developmentFeasibility": source_data.get("developmentFeasibility") or {},
            "cashFlow": source_data.get("cashFlow") or source_data.get("cashFlowRunResult") or {},
            "productMix": source_data.get("productMix") or source_data.get("product_mix") or [],
        }

    if normalized_page in {"pf", "risk"}:
        return {
            **common,
            "pfStructure": source_data.get("pfStructure") or source_data.get("pf_structure") or {},
            "riskRegister": source_data.get("riskRegister") or {},
            "creditReviewDecision": source_data.get("creditReviewDecision") or {},
            "latestPfCreditArtifact": _latest_chief_artifact_for_pod(chief_artifacts, "pf_credit"),
            "chiefArtifacts": chief_artifacts,
        }

    if normalized_page == "evidence":
        return {
            **common,
            "sourceDocuments": source_documents,
            **({"landEvidenceRecords": land_evidence_records} if land_evidence_records is not None else {}),
            "evidenceGraphEndpoint": f"/projects/{project_id}/evidence-graphs",
            "evidenceGraphDetailEndpointTemplate": f"/projects/{project_id}/orchestrations/{{run_id}}/evidence-graph",
        }

    if plan is not None:
        common.update({
            "agentPods": plan.get("agentPods") or [],
            "podRuns": plan.get("podRuns") or [],
            "taskGraph": plan.get("taskGraph") or {},
        })
    return {**common, "chiefArtifacts": chief_artifacts}


@router.get(
    "/{project_id}/workspace/document-review",
    response_model=DocumentReviewWorkspaceViewModel,
)
def get_project_document_review_workspace(
    project_id: str,
    request: Request,
):
    """Return one tenant-scoped, versioned Parser V2 review read model.

    Candidate, review, canonical-selection, and approved-snapshot records are
    assembled on the server after one project authorization boundary.  The
    payload remains read-only and candidate-first; it cannot promote a value or
    stand in for an ApprovedInputSnapshot.
    """

    authz = authorize_request(request, "evidence:read")
    project = authoritative_project(project_id, request)
    enforce_project_tenant_scope(request, project)
    user = authz.get("user") if isinstance(authz.get("user"), dict) else {}
    metadata = project.get("_meta") if isinstance(project.get("_meta"), dict) else {}
    tenant_id = str(
        user.get("tenantId")
        or metadata.get("tenantId")
        or metadata.get("tenant_id")
        or ""
    ).strip()
    if not tenant_id:
        if (
            str(authz.get("rbacMode") or "").lower() == "strict"
            or storage_backend() in {"postgres", "postgresql"}
        ):
            raise HTTPException(status_code=401, detail={"error": "tenant_required"})
        tenant_id = "default"

    store = parser_v2_record_store()
    try:
        runs = store.list_parsing_runs(tenant_id, project_id)
        document_versions = store.list_document_versions(tenant_id, project_id)
        candidates = store.list_candidates(tenant_id, project_id)
        conflicts = store.list_source_conflicts(tenant_id, project_id)
        reviews = store.list_review_decisions(tenant_id, project_id)
        selections = store.list_canonical_selections(tenant_id, project_id)
        snapshots = [
            approved_input_snapshot_wire_payload(item)
            for item in store.list_approved_input_snapshots(tenant_id, project_id)
        ]
        seals = store.list_candidate_set_seals(tenant_id, project_id)
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "document_review_authority_unavailable",
                "message": str(exc),
            },
        ) from exc

    documents_by_id = {
        str(item.get("documentVersionId") or ""): item
        for item in document_versions
        if str(item.get("documentVersionId") or "")
    }
    packages: list[Dict[str, Any]] = []
    for run in runs:
        run_id = str(run.get("parsingRunId") or "")
        document_version_id = str(run.get("documentVersionId") or "")
        document_version = documents_by_id.get(document_version_id)
        if not run_id or document_version is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "parser_run_lineage_incomplete",
                    "parsingRunId": run_id or None,
                    "documentVersionId": document_version_id or None,
                },
            )
        run_candidates = [
            item for item in candidates if item.get("parsingRunId") == run_id
        ]
        candidate_ids = {
            str(item.get("candidateId") or "") for item in run_candidates
        }
        run_conflicts = [
            item
            for item in conflicts
            if run_id in (item.get("sourceParsingRunIds") or [])
            or item.get("parsingRunId") == run_id
            or candidate_ids.intersection(
                str(value) for value in (item.get("candidateIds") or [])
            )
        ]
        run_reviews = [
            item for item in reviews if str(item.get("candidateId") or "") in candidate_ids
        ]
        run_selections = [
            item
            for item in selections
            if str(item.get("candidateId") or "") in candidate_ids
        ]
        selection_ids = {
            str(item.get("selectionId") or "") for item in run_selections
        }
        run_snapshots = [
            item
            for item in snapshots
            if selection_ids.intersection(
                str(value) for value in (item.get("selectionIds") or [])
            )
        ]
        run_seal = next(
            (
                item
                for item in seals
                if item.get("parsingRunId") == run_id
            ),
            None,
        )
        packages.append(
            {
                "schemaVersion": "realytics-parser-v2-run-package-v2",
                "candidateOnly": True,
                "authoritative": False,
                "projectProjectionMutated": False,
                "projectId": project_id,
                "parsingRun": run,
                "documentVersion": document_version,
                "candidates": run_candidates,
                "conflicts": run_conflicts,
                "candidateSetSeal": run_seal,
                "reviewDecisions": run_reviews,
                "canonicalSelections": run_selections,
                "approvedInputSnapshots": run_snapshots,
            }
        )

    source_documents = list_source_documents_use_case(
        project_source_document_read_repository(project_id, project),
        project_id,
    )
    glossary = list_glossary_candidates_use_case(
        project_id=project_id,
        limit=500,
    )
    result = build_document_review_workspace_read_model(
        project_id=project_id,
        source_documents=source_documents,
        run_packages=packages,
        glossary_candidates=glossary.get("candidates") or [],
    )
    record_sensitive_project_event(
        request,
        event_type="document_review_workspace_loaded",
        project_id=project_id,
        metadata={
            "snapshotId": result.get("snapshotId"),
            "snapshotHash": result.get("snapshotHash"),
            "viewModelVersion": result.get("viewModelVersion"),
            "sourceDocumentCount": len(source_documents),
            "parsingRunCount": len(runs),
            "reviewQueueCount": len(result.get("reviewQueue") or []),
        },
    )
    return result


@router.get("/{project_id}/workspace/context")
def get_project_workspace_context(
    project_id: str,
    request: Request,
    page: str = "overview",
    track: str | None = None,
):
    """Return page-specific REALYTICS OS context backed by the shared deal memory.

    Each workspace page gets its own data slice, but all slices are derived from
    the same hydrated project, Evidence Vault, Assumption Book, pod artifacts,
    and persisted Shared Deal State Graph.
    """
    if storage_backend() in {"postgres", "postgresql"}:
        authorize_request(request, "project:read")
        project = authoritative_project(project_id, request)
        enforce_project_tenant_scope(request, project)
        source_documents = list_source_documents_use_case(
            project_source_document_read_repository(project_id, project), project_id
        )
        parsed_facts = persisted_parsed_fact_package(project_id)
        chief_artifacts = list_chief_pod_artifact_records(project_id)
        project_record = project.get("project") if isinstance(project.get("project"), dict) else {}
        is_land_page = _normalize_workspace_page(page) == "land"
        land_records = None
        if is_land_page:
            try:
                land_records = list_land_evidence_records(project_id)
            except Exception:
                # A land-evidence read failure must remain distinct from an empty ledger.
                pass
        page_data = _workspace_page_data(
            project_id=project_id,
            page=page,
            source_data=project,
            plan=None,
            source_documents=source_documents,
            land_evidence_records=land_records,
            parsed_fact_package_data=parsed_facts,
            assumption_overrides=None,
            chief_artifacts=chief_artifacts,
        )
        unavailable_sections = ["assumptionOverrides", "missingInputs"]
        if land_records is None:
            unavailable_sections.insert(0, "landEvidence")
        # The normalized runtime cannot hydrate the compatibility app_state
        # document. Do not infer missing evidence or persist a partial graph.
        # The request middleware records access through the normalized audit log.
        return {
            "schemaVersion": "realytics-workspace-page-context-v1",
            "contextCompleteness": "partial",
            "unavailableSections": unavailable_sections,
            "projectId": project_id,
            "page": page,
            "normalizedPage": _normalize_workspace_page(page),
            "pageDataContract": _workspace_page_data_contract(page),
            "workflowTrack": track or project.get("workflowTrack") or project.get("workflow_track"),
            "projectMemory": {
                "projectId": project_id,
                "projectName": _project_display_name(project_id, project),
                "project": project_record,
                "status": project_record.get("status") or project.get("status"),
                "sourceDocumentCount": len(source_documents),
                **({"landEvidenceCount": len(land_records)} if land_records is not None else {}),
                "chiefArtifactCount": len(chief_artifacts),
                "updatedAt": (project.get("_meta") or {}).get("updatedAt"),
            },
            "pageData": page_data,
        }
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")

    source_data = _hydrated_project_source(project_id, {})
    if track:
        source_data["workflowTrack"] = track
    options = {"workflowTrack": track}
    plan = plan_chief_deal_orchestration_use_case(project_id, source_data, options)
    shared_graph_record = _persist_shared_deal_state_graph(project_id, plan, source="workspace_context")
    source_documents = list_source_documents_use_case(source_document_repository(), project_id)
    land_evidence_records = list_land_evidence_records(project_id)
    parsed_fact_package_data = persisted_parsed_fact_package(project_id)
    assumption_overrides = list_assumption_override_records(project_id)
    chief_artifacts = list_chief_pod_artifact_records(project_id)
    project_record = source_data.get("project") if isinstance(source_data.get("project"), dict) else {}

    context = {
        "schemaVersion": "realytics-workspace-page-context-v1",
        "projectId": project_id,
        "page": page,
        "normalizedPage": _normalize_workspace_page(page),
        "pageDataContract": _workspace_page_data_contract(page),
        "workflowTrack": (plan.get("taskGraph") or {}).get("workflowTrack") or track,
        "projectMemory": {
            "projectId": project_id,
            "projectName": _project_display_name(project_id, source_data),
            "project": project_record,
            "status": project_record.get("status") or source_data.get("status"),
            "sourceDocumentCount": len(source_documents),
            "landEvidenceCount": len(land_evidence_records),
            "assumptionOverrideCount": len(assumption_overrides),
            "chiefArtifactCount": len(chief_artifacts),
            "updatedAt": (source_data.get("_meta") or {}).get("updatedAt") or shared_graph_record.get("updatedAt"),
        },
        "dealStateGraph": shared_graph_record,
        "evidenceVault": (plan.get("sharedDealStateGraph") or {}).get("evidenceVault") or [],
        "assumptionBook": (plan.get("sharedDealStateGraph") or {}).get("assumptionBook") or [],
        "missingInputRegister": (plan.get("sharedDealStateGraph") or {}).get("missingInputRegister") or [],
        "taskGraph": plan.get("taskGraph") or {},
        "agentPods": plan.get("agentPods") or [],
        "podRuns": plan.get("podRuns") or [],
        "decisionGate": plan.get("decisionGate") or {},
        "pageData": _workspace_page_data(
            project_id=project_id,
            page=page,
            source_data=source_data,
            plan=plan,
            source_documents=source_documents,
            land_evidence_records=land_evidence_records,
            parsed_fact_package_data=parsed_fact_package_data,
            assumption_overrides=assumption_overrides,
            chief_artifacts=chief_artifacts,
        ),
    }
    record_sensitive_project_event(
        request,
        event_type="workspace_page_context_loaded",
        project_id=project_id,
        metadata={
            "page": page,
            "workflowTrack": context.get("workflowTrack"),
            "sourceDocumentCount": len(source_documents),
            "missingInputCount": len(context.get("missingInputRegister") or []),
        },
    )
    return context


@router.post("/{project_id}/orchestration/plan")
def plan_orchestration(project_id: str, request: Request, input_data: Dict[str, Any] | None = None):
    """Intent router: inspect available materials and return the workflow menu,
    missing data, and a recommended default before any agents run."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    payload = input_data or {}
    source_data = _hydrated_project_source(project_id, payload)
    if payload.get("track"):
        source_data["workflowTrack"] = payload.get("track")
    plan = plan_orchestration_use_case(project_id, source_data)
    prompt_artifact = persist_work_order_prompt_artifact(
        project_id,
        plan,
        created_by=user_id_from_request(request),
    )
    if prompt_artifact:
        plan["promptArtifact"] = {
            "artifactId": prompt_artifact.get("artifactId"),
            "artifactType": prompt_artifact.get("artifactType"),
            "workflow": prompt_artifact.get("workflow"),
            "createdAt": prompt_artifact.get("createdAt"),
        }
        record_sensitive_project_event(
            request,
            event_type="prompt_artifact_created",
            project_id=project_id,
            metadata={
                "artifactId": prompt_artifact.get("artifactId"),
                "artifactType": prompt_artifact.get("artifactType"),
                "workflow": prompt_artifact.get("workflow"),
                "source": "orchestration_plan",
            },
        )
    return plan


@router.get("/{project_id}/chief-orchestration/approval-readiness")
def get_chief_deal_approval_readiness(project_id: str, request: Request):
    """Assess approval from the same server records used by report policy."""
    from db.legal_review_database import legal_review_database_configured
    from use_cases.approval_readiness_gate import build_deal_state_approval_readiness

    project = authoritative_project(project_id, request)
    authorize_request(request, "project:read")
    enforce_project_tenant_scope(request, project)
    readiness = build_deal_state_approval_readiness(
        project_id,
        tenant_id=tenant_id_from_request(request),
    )
    if readiness.get("readable") is False:
        raise HTTPException(status_code=503, detail={
            "error": "approval_readiness_authority_unavailable",
            "message": "승인 준비에 필요한 서버 기록을 확인하지 못했습니다.",
        })
    return {
        **readiness,
        "assessmentStatus": "assessed" if readiness.get("latestCalculationRunCount") else "unassessed",
        "cureWriteAvailable": storage_backend() not in {"postgres", "postgresql"},
        "legalReviewWriteAvailable": (
            storage_backend() in {"postgres", "postgresql"} and legal_review_database_configured()
        ),
    }


@router.post("/{project_id}/chief-orchestration/plan")
def plan_chief_deal_orchestration(project_id: str, request: Request, input_data: Dict[str, Any] | None = None):
    """Chief Deal Orchestrator: build the AI Deal Room plan.

    This endpoint does not run pod agents. It creates a Shared Deal State Graph,
    Missing Input Register, Agent Pod plan, scenario placeholders, and a
    preliminary decision gate so the user can see what the orchestrator will do
    before any approval-sensitive analysis is treated as final.
    """
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    payload = input_data or {}
    normalized = storage_backend() in {"postgres", "postgresql"}
    # The plan describes saved analyses and does not apply new cures. The
    # normalized project already carries any saved cure projection.
    source_data = _hydrated_project_source(
        project_id, {} if normalized else payload, include_active_cures=not normalized,
    )
    workflow_track = payload.get("workflowTrack") or payload.get("workflow_track") or payload.get("track")
    if workflow_track:
        source_data["workflowTrack"] = workflow_track
    options = {
        "analysisPurpose": payload.get("analysisPurpose") or payload.get("analysis_purpose"),
        "workflowTrack": workflow_track,
    }
    plan = plan_chief_deal_orchestration_use_case(project_id, source_data, options)
    _persist_shared_deal_state_graph(project_id, plan, source="chief_orchestration_plan")
    record_sensitive_project_event(
        request,
        event_type="chief_orchestration_plan_created",
        project_id=project_id,
        metadata={
            "analysisPurpose": plan.get("analysisPurpose"),
            "workflowTrack": (plan.get("taskGraph") or {}).get("workflowTrack"),
            "podCount": len(plan.get("agentPods") or []),
            "missingInputCount": len((plan.get("sharedDealStateGraph") or {}).get("missingInputRegister") or []),
            "decision": (plan.get("decisionGate") or {}).get("decision"),
        },
    )
    return plan


def _persist_chief_recalculation_event(
    project_id: str, event: Dict[str, Any], *, request: Request | None = None,
) -> Dict[str, Any]:
    persisted = upsert_recalculation_event_record(project_id, event)
    metadata = {
        "eventId": persisted.get("eventId"),
        "trigger": persisted.get("trigger"),
        "sourcePod": persisted.get("sourcePod"),
        "targetPods": persisted.get("targetPods"),
        "status": persisted.get("status"),
    }
    if request is not None:
        record_sensitive_project_event(
            request, event_type="chief_recalculation_triggered", project_id=project_id,
            run_id=persisted.get("runId"), metadata=metadata,
        )
    elif storage_backend() not in {"postgres", "postgresql"}:
        record_user_activity_log({
            "eventType": "chief_recalculation_triggered", "projectId": project_id,
            "runId": persisted.get("runId"), "metadata": metadata,
        })
    return persisted


def _stored_chief_evidence_options(project_id: str, project: Dict[str, Any], options: Dict[str, Any]) -> Dict[str, Any]:
    tenant_id = _project_tenant_id(project_id)
    try:
        documents = resolve_stored_permit_documents(project_id, project, parser_v2_record_store(), tenant_id)
        return {**options, "parsedDocuments": documents}
    except Exception:
        # Preserve the existence of the original without promoting inaccessible review records.
        documents = resolve_stored_permit_documents(project_id, project, None, None)
        for document in documents:
            document["evidenceStage"] = "authority_unavailable"
        return {**options, "parsedDocuments": documents, "permitEvidenceStatus": "authority_unavailable"}


def _chief_request_options(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    options = dict(payload["options"]) if isinstance(payload.get("options"), dict) else {}
    track = payload.get("workflowTrack") or payload.get("workflow_track") or payload.get("track")
    if track:
        options["workflowTrack"] = track
    snake_ai_override = options.pop("ai_context", None)
    camel_ai_override = options.pop("aiContext", None)
    ai_override = snake_ai_override or camel_ai_override or payload.get("ai_context") or payload.get("aiContext")
    options["ai_context"] = ai_context_from_request(request, payload_ai_context=ai_override)
    if storage_backend() in {"postgres", "postgresql"}:
        for identity_key in ("tenantId", "tenant_id", "userId", "user_id", "actorRoles"):
            options.pop(identity_key, None)
        user = identity_context_from_request(request).get("user") or {}
        role = user.get("role")
        options["actorRoles"] = [str(role)] if role else []
    return options


@router.post("/{project_id}/chief-orchestration/run-pod")
def run_chief_deal_pod(project_id: str, request: Request, input_data: Dict[str, Any] | None = None):
    """Chief Deal Orchestrator: execute a single agent pod and trigger downstream events."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:analyze")
    payload = input_data or {}
    pod_key = payload.get("podKey")
    if not pod_key:
        raise HTTPException(status_code=400, detail="podKey is required")
    options = _chief_request_options(request, payload)

    project_data = _project_store()[project_id]
    pending_work_ids = {row.get("workOrderId") for row in
                        (project_data.get("chief_orchestration_state") or {}).get("workOrders") or []
                        if row.get("podKey") == pod_key and row.get("status") == "queued"}
    try:
        pod_runner = run_chief_deal_pod_with_cascade_use_case if options.get("autoCascade") else run_chief_deal_pod_use_case
        options = _stored_chief_evidence_options(project_id, project_data, options)
        plan = pod_runner(
            project_id,
            pod_key,
            project_data,
            options,
            artifact_sink=lambda record: upsert_chief_pod_artifact_record(project_id, record),
            review_sink=lambda record: upsert_pod_ai_review_record(project_id, record),
            event_sink=lambda event: _persist_chief_recalculation_event(project_id, event, request=request),
            calculation_sink=lambda record: upsert_calculation_run_record(project_id, record),
            handoff_sink=lambda record: upsert_agent_handoff_record(project_id, record),
        )
    except (ValueError, RuntimeError) as e:
        failed_work = [row for row in (project_data.get("chief_orchestration_state") or {}).get("workOrders") or []
                       if row.get("workOrderId") in pending_work_ids and row.get("status") in {"failed", "needs_material"}]
        if failed_work:
            persist_project(project_id)
            record_sensitive_project_event(request, event_type="chief_pod_work_order_failed", project_id=project_id,
                metadata={"podKey": pod_key, "workOrderIds": [row["workOrderId"] for row in failed_work]})
        if isinstance(e, ValueError):
            raise HTTPException(status_code=400, detail=str(e)) from e
        if failed_work:
            raise HTTPException(status_code=502, detail="Pod 추가 업무 실행에 실패했습니다. 저장된 업무 상태를 확인하세요.") from e
        raise

    actor = str(options["ai_context"].get("user_id") or "chief_deal_orchestrator")
    plan["parsedFactValidation"] = _validate_supplement_and_revalidate_parsed_facts(
        project_id,
        str(pod_key),
        project_data=project_data,
        source_artifact_key=_latest_pod_artifact_key_from_plan(plan, str(pod_key)),
        actor=actor,
    )
    _persist_shared_deal_state_graph(project_id, plan, source=f"chief_pod:{pod_key}")

    record_sensitive_project_event(
        request,
        event_type="chief_orchestration_pod_executed",
        project_id=project_id,
        metadata={
            "podKey": pod_key,
            "status": "completed",
            "trigger": options.get("trigger"),
        },
    )
    return plan


@router.post("/{project_id}/chief-orchestration/run-all-pods")
def run_chief_deal_all_pods(project_id: str, request: Request, input_data: Dict[str, Any] | None = None):
    """Chief Deal Orchestrator: execute the selected track's pod graph in order."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:analyze")
    payload = input_data or {}
    options = _chief_request_options(request, payload)

    project_data = _project_store()[project_id]
    plan = run_chief_deal_all_pods_use_case(
        project_id,
        project_data,
        _stored_chief_evidence_options(project_id, project_data, options),
        artifact_sink=lambda record: upsert_chief_pod_artifact_record(project_id, record),
        review_sink=lambda record: upsert_pod_ai_review_record(project_id, record),
        event_sink=lambda event: _persist_chief_recalculation_event(project_id, event, request=request),
        calculation_sink=lambda record: upsert_calculation_run_record(project_id, record),
        handoff_sink=lambda record: upsert_agent_handoff_record(project_id, record),
    )

    completed_artifact_keys = _completed_pod_artifact_keys_from_plan(plan)
    actor = str(options["ai_context"].get("user_id") or "chief_deal_orchestrator")
    validation_results = [
        _validate_supplement_and_revalidate_parsed_facts(
            project_id,
            pod_key,
            project_data=project_data,
            source_artifact_key=artifact_key,
            actor=actor,
        )
        for pod_key, artifact_key in completed_artifact_keys.items()
    ]
    plan["parsedFactValidations"] = {
        "schemaVersion": "realytics-parsed-fact-pod-validation-batch-v1",
        "projectId": project_id,
        "count": len(validation_results),
        "results": validation_results,
        "summary": {
            "findingCount": sum(len(item.get("findings") or []) for item in validation_results),
            "riskIssueCount": sum(int((item.get("summary") or {}).get("riskIssueCount") or 0) for item in validation_results),
            "autoSupplementCreatedRecordCount": sum(
                int(((item.get("autoSupplement") or {}).get("summary") or {}).get("createdRecordCount") or 0)
                for item in validation_results
            ),
        },
    }
    _persist_shared_deal_state_graph(project_id, plan, source="chief_pod:run_all")

    run_all_summary = plan.get("runAllSummary") or {}
    record_sensitive_project_event(
        request,
        event_type="chief_orchestration_all_pods_executed",
        project_id=project_id,
        metadata={
            "workflowTrack": run_all_summary.get("workflowTrack"),
            "status": run_all_summary.get("status"),
            "requestedPodCount": run_all_summary.get("requestedPodCount"),
            "completedCount": run_all_summary.get("completedCount"),
            "blockedCount": run_all_summary.get("blockedCount"),
        },
    )
    return plan


def _completed_pod_keys(project_id: str) -> set:
    """Completed artifacts excluding currently failed, blocked or stale Pod runs."""
    keys = set()
    runs = (_project_store().get(project_id, {}).get("chief_orchestration_state") or {}).get("podRuns") or {}
    for record in list_chief_pod_artifact_records(project_id):
        pod_key = record.get("podKey") or record.get("pod_key")
        current_status = (runs.get(str(pod_key)) or {}).get("status")
        if pod_key and record.get("status", "completed") == "completed" and current_status not in {"blocked", "failed", "needs_recalculation"}:
            keys.add(str(pod_key))
    return keys


def _reanalyze_after_evidence(
    project_id: str, *, changed_document_types: list[str] | None,
    options: Dict[str, Any] | None = None, request: Request | None = None,
) -> Dict[str, Any]:
    """Re-run still-open + document-affected pods with the standard persistence sinks."""
    project_data = _project_store()[project_id]
    def validated_runner(pid, pod_key, data, runner_options, **sinks):
        plan = run_chief_deal_pod_use_case(pid, pod_key, data, runner_options, **sinks)
        plan["parsedFactValidation"] = _validate_supplement_and_revalidate_parsed_facts(
            pid, pod_key, project_data=data,
            source_artifact_key=_latest_pod_artifact_key_from_plan(plan, pod_key),
            actor="evidence_reanalysis",
        )
        _persist_shared_deal_state_graph(pid, plan, source=f"evidence_reanalysis:{pod_key}")
        return plan
    summary = reanalyze_after_evidence_use_case(
        project_id,
        project_data,
        completed_pod_keys=_completed_pod_keys(project_id),
        run_pod=validated_runner,
        changed_document_types=changed_document_types,
        options=_stored_chief_evidence_options(project_id, project_data, options or {}),
        artifact_sink=lambda record: upsert_chief_pod_artifact_record(project_id, record),
        review_sink=lambda record: upsert_pod_ai_review_record(project_id, record),
        event_sink=lambda event: _persist_chief_recalculation_event(project_id, event, request=request),
        calculation_sink=lambda record: upsert_calculation_run_record(project_id, record),
        handoff_sink=lambda record: upsert_agent_handoff_record(project_id, record),
    )
    if summary.get("triggered"):
        persist_project(project_id)
    return summary


@router.post("/{project_id}/chief-orchestration/reanalyze")
def reanalyze_chief_deal(project_id: str, request: Request, input_data: Dict[str, Any] | None = None):
    """Smart re-analysis after new evidence: re-run the blocked / not-yet-completed
    pods plus the pods the new document type feeds. Each pod re-run re-validates
    parsed facts, auto-supplements, regenerates the AI review, and cascades. Pods
    that still lack evidence stay blocked honestly."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:analyze")
    payload = input_data or {}
    doc_types = payload.get("changedDocumentTypes")
    doc_types = doc_types if isinstance(doc_types, list) else None
    options = _chief_request_options(request, payload)
    summary = _reanalyze_after_evidence(project_id, changed_document_types=doc_types, options=options, request=request)
    record_sensitive_project_event(
        request,
        event_type="chief_orchestration_reanalyzed_on_evidence",
        project_id=project_id,
        metadata={"triggered": summary.get("triggered"), "rerun": summary.get("rerunCount"), "unblocked": summary.get("newlyUnblocked")},
    )
    return summary


@router.get("/{project_id}/chief-orchestration/artifacts")
def list_chief_deal_artifacts(project_id: str, request: Request, pod_key: str | None = None):
    """List persisted Chief Deal Room pod artifacts for audit/review."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    state = _project_store()[project_id].get("chief_orchestration_state") or {}
    records = list_chief_pod_artifact_records(project_id, pod_key=pod_key)
    if not records:
        artifacts = state.get("artifacts") or {}
        records = [
            artifact
            for artifact in artifacts.values()
            if isinstance(artifact, dict) and (not pod_key or artifact.get("podKey") == pod_key)
        ]
        records.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
    return {
        "schemaVersion": "realytics-chief-pod-artifact-list-v1",
        "projectId": project_id,
        "podKey": pod_key,
        "records": records,
    }


@router.get("/{project_id}/chief-orchestration/pod-ai-reviews")
def list_chief_deal_pod_ai_reviews(
    project_id: str,
    request: Request,
    pod_key: str | None = None,
    review_status: str | None = None,
    source_artifact_key: str | None = None,
    limit: int = 100,
):
    """List persisted Pod AI Reviewer records for Chief Deal Room audit/review."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    safe_limit = max(1, min(500, int(limit or 100)))
    records = list_pod_ai_review_records(
        project_id,
        pod_key=pod_key,
        review_status=review_status,
        source_artifact_key=source_artifact_key,
        limit=safe_limit,
    )
    return {
        "schemaVersion": "realytics-pod-ai-review-list-v1",
        "projectId": project_id,
        "podKey": pod_key,
        "reviewStatus": review_status,
        "count": len(records),
        "records": records,
    }


@router.get("/{project_id}/chief-orchestration/pod-ai-reviews/{review_id:path}")
def get_chief_deal_pod_ai_review(project_id: str, review_id: str, request: Request):
    """Read a persisted Pod AI Reviewer record by ID."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    record = get_pod_ai_review_record(project_id, review_id)
    if not record:
        raise HTTPException(status_code=404, detail="Pod AI review not found")
    return record


@router.get("/{project_id}/chief-orchestration/agent-runs")
def list_chief_agent_runs(
    project_id: str,
    request: Request,
    status: str | None = None,
    limit: int = 100,
):
    """List durable Agent executions associated with the current project."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    records = list_agent_run_records(
        project_id=project_id,
        status=status,
        limit=max(1, min(500, int(limit or 100))),
    )
    return {
        "schemaVersion": "realytics-agent-run-list-v1",
        "projectId": project_id,
        "status": status,
        "count": len(records),
        "records": records,
    }


@router.get("/{project_id}/chief-orchestration/agent-runs/{agent_run_id}")
def get_chief_agent_run(project_id: str, agent_run_id: str, request: Request):
    """Read a durable Agent run with ordered steps and secret-free Brain usage."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    record = get_agent_run_record(agent_run_id)
    if not record or str(record.get("projectId") or "") != str(project_id):
        raise HTTPException(status_code=404, detail="Agent run not found")
    return {
        "schemaVersion": "realytics-agent-run-detail-v1",
        "projectId": project_id,
        "run": record,
        "steps": list_agent_run_step_records(agent_run_id),
        "brainUsageEvents": list_brain_usage_event_records(agent_run_id),
    }


@router.post("/{project_id}/chief-orchestration/agent-runs/{agent_run_id}/cancel")
def cancel_chief_agent_run(
    project_id: str,
    agent_run_id: str,
    request: Request,
    payload: Dict[str, Any] | None = None,
):
    """Request cooperative cancellation; terminal runs remain immutable."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authz = authorize_request(request, "project:analyze")
    record = get_agent_run_record(agent_run_id)
    if not record or str(record.get("projectId") or "") != str(project_id):
        raise HTTPException(status_code=404, detail="Agent run not found")
    reason = str((payload or {}).get("reason") or "user_requested")
    cancelled = StorageAgentRunRepository().request_cancellation(agent_run_id, reason=reason)
    record_sensitive_project_event(
        request,
        event_type="agent_run_cancellation_requested",
        project_id=project_id,
        run_id=agent_run_id,
        metadata={
            "reason": reason,
            "requestedBy": (authz.get("user") or {}).get("userId"),
            "runStatus": cancelled.status.value,
        },
    )
    return cancelled.model_dump(by_alias=True, mode="json")


@router.get("/{project_id}/chief-orchestration/recalculation-events")
def list_chief_deal_recalculation_events(
    project_id: str,
    request: Request,
    run_id: str | None = None,
    source_pod: str | None = None,
    target_pod: str | None = None,
    status: str | None = None,
    limit: int = 100,
):
    """List persisted Event Bus recalculation triggers for the Deal Room."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    safe_limit = max(1, min(500, int(limit or 100)))
    records = list_recalculation_event_records(
        project_id,
        run_id=run_id,
        source_pod=source_pod,
        target_pod=target_pod,
        status=status,
        limit=safe_limit,
    )
    return {
        "schemaVersion": "realytics-recalculation-event-list-v1",
        "projectId": project_id,
        "count": len(records),
        "records": records,
    }


@router.get("/{project_id}/chief-orchestration/handoffs")
def list_chief_deal_handoffs(
    project_id: str,
    request: Request,
    from_agent: str | None = None,
    to_agent: str | None = None,
    status: str | None = None,
    limit: int = 100,
):
    """List persisted AgentHandoffEnvelope records for Pod-to-Pod audit."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    records = list_agent_handoff_records(
        project_id,
        from_agent=from_agent,
        to_agent=to_agent,
        status=status,
        limit=max(1, min(500, int(limit or 100))),
    )
    return {
        "schemaVersion": "realytics-agent-handoff-list-v1",
        "projectId": project_id,
        "count": len(records),
        "records": records,
    }


@router.get("/{project_id}/chief-orchestration/events")
def list_chief_deal_events(project_id: str, request: Request, limit: int = 100):
    """Event Bus history: recalculation triggers + learning-loop events for audit.

    Combines the in-project orchestration state (fast, current) with the durable
    activity log (queryable across restarts/state rebuilds)."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    limit = max(1, min(500, limit))

    state = _project_store()[project_id].get("chief_orchestration_state") or {}
    state_events = [e for e in (state.get("learningLoopEvents") or []) if isinstance(e, dict)]

    durable = list_recalculation_event_records(project_id, limit=limit)
    if not durable:
        durable = [
            {
                "eventId": (row.get("metadata") or {}).get("eventId") or row.get("eventId"),
                "eventType": "recalculation_triggered",
                "trigger": (row.get("metadata") or {}).get("trigger"),
                "sourcePod": (row.get("metadata") or {}).get("sourcePod"),
                "targetPods": (row.get("metadata") or {}).get("targetPods"),
                "timestamp": (row.get("metadata") or {}).get("timestamp") or row.get("createdAt"),
                "durable": True,
            }
            for row in list_user_activity_logs(project_id=project_id, event_type="chief_recalculation_triggered", limit=limit)
        ]

    merged: Dict[str, Dict[str, Any]] = {}
    for event in [*state_events, *durable]:
        key = str(event.get("eventId") or f"{event.get('trigger')}:{event.get('timestamp') or event.get('createdAt')}")
        merged.setdefault(key, event)
    events = sorted(merged.values(), key=lambda e: str(e.get("timestamp") or e.get("createdAt") or ""), reverse=True)[:limit]
    return {
        "schemaVersion": "realytics-chief-orchestration-events-v1",
        "projectId": project_id,
        "count": len(events),
        "events": events,
    }


@router.get("/{project_id}/chief-orchestration/artifacts/{artifact_key:path}")
def get_chief_deal_artifact(project_id: str, artifact_key: str, request: Request):
    """Read a persisted Chief Deal Room pod artifact by key."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    record = get_chief_pod_artifact_record(project_id, artifact_key)
    if not record:
        record = ((_project_store()[project_id].get("chief_orchestration_state") or {}).get("artifacts") or {}).get(artifact_key)
    if not record:
        raise HTTPException(status_code=404, detail="Chief pod artifact not found")
    return record


@router.get("/{project_id}/prompt-artifacts")
def list_project_prompt_artifacts(project_id: str, request: Request, artifact_type: str | None = None, workflow: str | None = None):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    return {
        "schemaVersion": "realytics-prompt-artifact-list-v1",
        "projectId": project_id,
        "records": [
            {
                "artifactId": record.get("artifactId"),
                "artifactType": record.get("artifactType"),
                "workflow": record.get("workflow"),
                "title": record.get("title"),
                "createdBy": record.get("createdBy"),
                "createdAt": record.get("createdAt"),
                "updatedAt": record.get("updatedAt"),
                "summary": record.get("summary"),
            }
            for record in list_prompt_artifact_records(project_id, artifact_type=artifact_type, workflow=workflow)
        ],
    }


@router.get("/{project_id}/prompt-artifacts/{artifact_id}")
def get_project_prompt_artifact(project_id: str, artifact_id: str, request: Request):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    record = get_prompt_artifact_record(project_id, artifact_id)
    if not record:
        raise HTTPException(status_code=404, detail="Prompt artifact not found")
    return record


@router.post("/{project_id}/prompt-artifacts")
def upsert_project_prompt_artifact(project_id: str, request: Request, input_data: Dict[str, Any]):
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:write")
    artifact = upsert_prompt_artifact_record(
        project_id,
        {
            **(input_data or {}),
            "createdBy": (input_data or {}).get("createdBy") or user_id_from_request(request),
        },
    )
    record_sensitive_project_event(
        request,
        event_type="prompt_artifact_upserted",
        project_id=project_id,
        metadata={
            "artifactId": artifact.get("artifactId"),
            "artifactType": artifact.get("artifactType"),
            "workflow": artifact.get("workflow"),
        },
    )
    return artifact


def _project_name(project_id: str) -> str:
    return (_project_store().get(project_id, {}).get("project", {}) or {}).get("name") or project_id


@router.get("/{project_id}/developability-score")
def get_project_developability_score(project_id: str):
    """Composite developability score (개발가능성 점수화) from land/regulation/feasibility/market."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    source_data = _hydrated_project_source(project_id, {})
    return score_developability_use_case(source_data)


@router.get("/{project_id}/monitoring")
def get_project_monitoring(project_id: str):
    """Live PF covenant monitoring (DSCR/LTV/LTC/Exit/sales-rate vs thresholds + stress EOD signal)."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    source_data = _hydrated_project_source(project_id, {})
    return monitor_covenants_use_case(source_data)


@router.get("/{project_id}/map")
def get_project_map_intelligence(
    project_id: str,
    resolve_geometry: bool = False,
    geometry_limit: int = Query(30, ge=0, le=300),
    focus_pnu: str | None = Query(None, pattern=r"^[0-9]{10}[12][0-9]{8}$"),
):
    """Map Intelligence: parcel GeoJSON + the deck's 6-layer spatial availability.

    Deterministic and non-fabricating — parcels with no resolved geometry are
    returned as unlocated GeoJSON features and flagged. With
    `resolve_geometry=true` and a configured VWorld key, parcel boundaries are
    fetched live from the VWorld cadastral layer by PNU (capped, best-effort).
    """
    project = _project_store().get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    source_data = attach_persisted_land_evidence(project_id, project)
    parcels = project_map_parcels(source_data)
    geometry_resolution = None
    if resolve_geometry:
        configured = vworld_configured()
        attempted = 0
        deadline = monotonic() + 12
        deferred = False

        def resolve(pnu: str) -> Dict[str, Any] | None:
            nonlocal attempted, deferred
            remaining = deadline - monotonic()
            if remaining < 0.5:
                deferred = True
                return None
            attempted += 1
            geometry, _ = fetch_parcel_geometry_by_pnu(pnu, timeout=min(4.0, remaining))
            return geometry

        resolved = (
            resolve_and_attach_geometry(parcels, resolve, limit=geometry_limit, focus_pnu=focus_pnu)
            if configured else 0
        )
        if not configured:
            status = "not_configured"
        elif not attempted:
            status = "no_eligible_parcels"
        elif resolved < attempted or deferred:
            status = "partial" if resolved else "unresolved"
        else:
            status = "resolved"
        geometry_resolution = {
            "source": "vworld_cadastral",
            "status": status,
            "requestedLimit": geometry_limit,
            "attemptedCount": attempted,
            "resolvedCount": resolved,
            "fetchedFromVworld": resolved,
        }
    payload = build_project_map_intelligence(source_data, parcels=parcels)
    if geometry_resolution is not None:
        payload["geometryResolution"] = geometry_resolution
    return payload


@router.put("/{project_id}/monitoring/thresholds")
def set_project_monitoring_thresholds(project_id: str, request: Request, input_data: Dict[str, Any] | None = None):
    """Persist project-specific covenant thresholds used by live monitoring.

    Accepts {"thresholds": {dscr, ltv, ltc, exit_sale_rate, sales_rate}} — only
    known keys with positive numeric values are saved; the rest fall back to the
    standard PF defaults.
    """
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    payload = input_data or {}
    raw = payload.get("thresholds", payload)
    try:
        result = set_monitoring_thresholds_use_case(project_repository(), project_id, raw)
    except LookupError:
        raise HTTPException(status_code=404, detail="Project not found")
    record_sensitive_project_event(
        request,
        event_type="monitoring_thresholds_updated",
        project_id=project_id,
        metadata={"overrideKeys": sorted(result.get("overrides", {}).keys())},
    )
    return result


def _snapshot_history_by_project() -> Dict[str, list[Dict[str, Any]]]:
    """Group stored snapshots into per-project metric history for trend arrows.

    Uses the snapshot caseComparisonSeed (ltv/ltc/exitSaleRate) — the only
    metrics persisted per run — mapped onto the covenant metric keys.
    """
    history: Dict[str, list[Dict[str, Any]]] = {}
    for snapshot in project_snapshot_repository(local_compatibility=True).list_all():
        project_id = snapshot.get("projectId")
        seed = snapshot.get("caseComparisonSeed") or {}
        metrics = {}
        if isinstance(seed.get("ltv"), (int, float)):
            metrics["ltv"] = seed["ltv"]
        if isinstance(seed.get("ltc"), (int, float)):
            metrics["ltc"] = seed["ltc"]
        if isinstance(seed.get("exitSaleRate"), (int, float)):
            metrics["exit_sale_rate"] = seed["exitSaleRate"]
        if not project_id or not metrics:
            continue
        history.setdefault(project_id, []).append({
            "createdAt": snapshot.get("createdAt"),
            "runId": snapshot.get("runId"),
            "metrics": metrics,
        })
    return history


@monitoring_router.get("/portfolio")
def get_portfolio_monitoring():
    """Portfolio-wide live PF monitoring: per-project covenant status, a
    critical-first alert feed, and a trend arrow vs each project's prior run."""
    history = _snapshot_history_by_project()
    entries: list[Dict[str, Any]] = []
    for project_id in list(_project_store().keys()):
        source_data = _hydrated_project_source(project_id, {})
        entries.append({
            "projectId": project_id,
            "name": _project_name(project_id),
            "monitoring": monitor_covenants_use_case(source_data),
            "history": history.get(project_id),
        })
    return build_portfolio_monitoring(entries)


@router.post("/{project_id}/terms/annotate")
def annotate_project_terms(project_id: str, input_data: Dict[str, Any] | None = None):
    """Attach controlled-vocabulary identity (termId + canonicalField + sourceLocator)
    to extracted terms in the project's parsed data, for report and audit linkage."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    source_data = _hydrated_project_source(project_id, input_data or {})
    return annotate_terms_use_case(source_data, project_id=project_id)


@router.post("/{project_id}/kakao/intake-plan")
def kakao_intake_plan(project_id: str, input_data: Dict[str, Any] | None = None):
    """KakaoTalk intake → intent menu. Runs the Orchestration Planner and returns
    both the plan and a KakaoTalk-safe message presenting the workflow choices."""
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    source_data = _hydrated_project_source(project_id, input_data or {})
    plan = plan_orchestration_use_case(project_id, source_data)
    kakao_message = build_kakao_intake_plan_message(project_id, _project_name(project_id), plan)
    record_user_activity_log({
        "eventType": "kakao_message_preview",
        "userId": "system",
        "projectId": project_id,
        "metadata": {
            "eventType": "analysis_plan_ready",
            "readyToSend": kakao_message.get("readyToSend"),
            "recommendedWorkflow": plan.get("recommendedWorkflow"),
            "messageMode": "preview_only",
        },
    })
    return {"plan": plan, "kakaoMessage": kakao_message}


@router.post("/{project_id}/orchestration/run-workflow")
def run_orchestration_workflow(
    project_id: str,
    request: Request,
    input_data: Dict[str, Any] | None = None,
):
    """Run only the agents required by a user-selected workflow key.

    When ``channel == 'kakao'`` the response also carries a KakaoTalk-safe
    completion message so the intake flow can notify the user of the result.
    """
    if project_id not in _project_store():
        raise HTTPException(status_code=404, detail="Project not found")
    authorization = authorize_request(request, "project:analyze")
    if not authorization.get("authenticated"):
        raise HTTPException(
            status_code=401,
            detail={
                "error": "authentication_required",
                "scope": "orchestration_workflow",
            },
        )
    enforce_project_tenant_scope(request, _project_store()[project_id])
    input_data = input_data or {}
    workflow = input_data.get("workflow")
    if not workflow:
        raise HTTPException(status_code=422, detail="Missing 'workflow' selection")
    source_data = _hydrated_project_source(project_id, input_data)
    if workflow == "full_pf_review":
        options = dict(input_data.get("options") or {})
        options["user_id"] = user_id_from_request(request)
        options["requested_by"] = user_id_from_request(request)
        options["tenant_id"] = tenant_id_from_request(request)
        run = run_and_save_pf_review_use_case(
            orchestration_repository(request), project_id, source_data, options,
        )
    else:
        run = run_workflow_use_case(project_id, source_data, workflow, input_data.get("options", {}))

    if str(input_data.get("channel") or "").lower() == "kakao":
        message = build_kakao_workflow_result_message(
            project_id, _project_name(project_id), workflow, run.get("status"),
        )
        record_user_activity_log({
            "eventType": "kakao_message_preview",
            "userId": "system",
            "projectId": project_id,
            "metadata": {"eventType": "workflow_result_ready", "workflow": workflow,
                         "readyToSend": message.get("readyToSend"), "messageMode": "preview_only"},
        })
        if isinstance(run, dict):
            run = {**run, "kakaoMessage": message}
    return run


@router.post("/{project_id}/orchestrations/pf-review")
def start_pf_review_orchestration(
    project_id: str,
    request: Request,
    input_data: Dict[str, Any] | None = None,
):
    project = authoritative_project(project_id, request)
    authorization = authorize_request(request, "project:analyze")
    if not authorization.get("authenticated"):
        raise HTTPException(
            status_code=401,
            detail={
                "error": "authentication_required",
                "scope": "pf_review_orchestration",
            },
        )
    enforce_project_tenant_scope(request, project)

    input_data = input_data or {}
    source_data = _hydrated_project_source(
        project_id,
        input_data,
        project,
    )
    options = dict(input_data.get("options") or {})
    options["user_id"] = user_id_from_request(request)
    options["requested_by"] = user_id_from_request(request)
    options["tenant_id"] = tenant_id_from_request(request)
    return run_and_save_pf_review_use_case(
        orchestration_repository(request),
        project_id,
        source_data,
        options,
    )


@router.get("/{project_id}/orchestrations")
def list_project_orchestrations(project_id: str, request: Request):
    project = authoritative_project(project_id, request)
    authorize_request(request, "project:read")
    enforce_project_tenant_scope(request, project)
    return orchestration_repository(request).list_metadata(project_id)

@router.get("/{project_id}/orchestrations/{run_id}")
def get_project_orchestration(project_id: str, run_id: str, request: Request):
    project = authoritative_project(project_id, request)
    authorize_request(request, "project:read")
    enforce_project_tenant_scope(request, project)
    run = orchestration_repository(request).get(project_id, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Orchestration run not found")
    return run


@router.get("/{project_id}/orchestrations/{run_id}/credit-documents")
def get_project_orchestration_credit_documents(project_id: str, run_id: str, request: Request):
    """Return the Term Sheet and IC Memo generated during a PF review run."""
    project = authoritative_project(project_id, request)
    authorize_request(request, "project:read")
    enforce_project_tenant_scope(request, project)

    run = orchestration_repository(request).get(project_id, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Orchestration run not found")
    artifacts = run.get("artifacts", {})
    term_sheet = artifacts.get("termSheet")
    ic_memo = artifacts.get("icMemo")
    if term_sheet is None and ic_memo is None:
        raise HTTPException(status_code=404, detail="Credit documents were not generated for this run")
    return {
        "schemaVersion": "realytics-credit-documents-v1",
        "projectId": project_id,
        "runId": run_id,
        "termSheet": term_sheet,
        "termSheetStages": artifacts.get("termSheetStages") or {},
        "icMemo": ic_memo,
    }


CREDIT_DOCUMENT_ARTIFACT_KEYS = {"term-sheet": "termSheet", "ic-memo": "icMemo"}
CREDIT_DOCUMENT_EXPORT_FORMATS = {"markdown", "md", "pdf", "docx"}


@router.get("/{project_id}/orchestrations/{run_id}/credit-documents/{document}/export")
def export_project_credit_document(
    project_id: str,
    run_id: str,
    document: str,
    request: Request,
    format: str = "pdf",
    stage: str | None = None,
):
    """Download a generated Term Sheet or IC Memo as markdown, PDF, or DOCX.

    For a term sheet, `stage` (bridge / main_pf) selects the financing-stage
    variant; defaults to the auto-detected stage stored on the run.
    """
    project = authoritative_project(project_id, request)
    authz = authorize_request(request, "report:export")
    enforce_project_tenant_scope(request, project)

    artifact_key = CREDIT_DOCUMENT_ARTIFACT_KEYS.get(document)
    if not artifact_key:
        raise HTTPException(status_code=400, detail="document must be 'term-sheet' or 'ic-memo'")
    export_format = format.lower().strip()
    if export_format not in CREDIT_DOCUMENT_EXPORT_FORMATS:
        raise HTTPException(status_code=400, detail="format must be markdown, pdf, or docx")
    if export_format == "md":
        export_format = "markdown"

    run = orchestration_repository(request).get(project_id, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Orchestration run not found")
    artifacts = run.get("artifacts", {}) or {}
    document_payload = artifacts.get(artifact_key)
    if artifact_key == "termSheet" and stage:
        normalized_stage = stage.strip().lower()
        if normalized_stage not in {"bridge", "main_pf"}:
            raise HTTPException(status_code=400, detail="stage must be 'bridge' or 'main_pf'")
        document_payload = (artifacts.get("termSheetStages") or {}).get(normalized_stage) or document_payload
    if not document_payload:
        raise HTTPException(status_code=404, detail="Credit document was not generated for this run")

    from agents.report_export_agent import ReportExportAgent

    record = {
        "report_id": f"{document}-{run_id}",
        "title": document_payload.get("documentType") or document,
        "report_markdown": document_payload.get("markdown") or "",
        "report": {},
        "version": 1,
    }
    result = ReportExportAgent().export(record, export_format)
    content = result.get("content", "")
    if result.get("content_encoding") == "base64":
        content = base64.b64decode(content)
    elif isinstance(content, str):
        content = content.encode("utf-8")

    record_sensitive_project_event(
        request,
        event_type="credit_document_export_download",
        project_id=project_id,
        run_id=run_id,
        metadata={
            "document": document,
            "format": export_format,
            "fileName": result.get("file_name"),
            "authorization": authz.get("authorization"),
        },
    )
    return Response(
        content=content,
        media_type=result.get("content_type", "application/octet-stream"),
        headers={
            "Content-Disposition": f'attachment; filename="{result.get("file_name", f"{document}.{export_format}")}"'
        },
    )


@router.get("/{project_id}/risk-registers")
def list_project_risk_registers(project_id: str, request: Request):
    project = authoritative_project(project_id, request)
    authorize_request(request, "project:read")
    enforce_project_tenant_scope(request, project)
    return {
        "schemaVersion": "realytics-risk-register-list-v1",
        "projectId": project_id,
        "records": [
            {
                "registerId": record.get("registerId"),
                "runId": record.get("runId"),
                "status": record.get("status"),
                "riskCount": record.get("riskCount", len(record.get("risks") or [])),
                "reviewSummary": record.get("reviewSummary") or {},
                "updatedAt": record.get("updatedAt"),
            }
            for record in list_risk_register_records(project_id)
        ],
    }

@router.get("/{project_id}/orchestrations/{run_id}/risk-register")
def get_project_orchestration_risk_register(project_id: str, run_id: str, request: Request):
    project = authoritative_project(project_id, request)
    authorize_request(request, "project:read")
    enforce_project_tenant_scope(request, project)
    record = get_risk_register_record(project_id, run_id)
    if not record:
        run = orchestration_repository(request).get(project_id, run_id)
        record = (run or {}).get("artifacts", {}).get("riskRegister")
    if not record:
        raise HTTPException(status_code=404, detail="Risk register not found")
    return record

@router.get("/{project_id}/evidence-graphs")
def list_project_evidence_graphs(project_id: str, request: Request):
    project = authoritative_project(project_id, request)
    authorize_request(request, "project:read")
    enforce_project_tenant_scope(request, project)
    return {
        "schemaVersion": "realytics-evidence-graph-list-v1",
        "projectId": project_id,
        "records": [
            {
                "graphId": record.get("graphId"),
                "runId": record.get("runId"),
                "nodeCount": record.get("nodeCount", (record.get("summary") or {}).get("nodeCount")),
                "edgeCount": record.get("edgeCount", (record.get("summary") or {}).get("edgeCount")),
                "riskNodeCount": record.get("riskNodeCount", (record.get("summary") or {}).get("riskNodeCount")),
                "sourceLocatorCount": record.get("sourceLocatorCount", (record.get("summary") or {}).get("sourceLocatorCount")),
                "updatedAt": record.get("updatedAt"),
            }
            for record in list_evidence_graph_records(project_id)
        ],
    }

@router.get("/{project_id}/orchestrations/{run_id}/evidence-graph")
def get_project_orchestration_evidence_graph(project_id: str, run_id: str, request: Request):
    project = authoritative_project(project_id, request)
    authorize_request(request, "project:read")
    enforce_project_tenant_scope(request, project)
    record = get_evidence_graph_record(project_id, run_id)
    if not record:
        run = orchestration_repository(request).get(project_id, run_id)
        record = (run or {}).get("artifacts", {}).get("evidenceGraph")
    if not record:
        raise HTTPException(status_code=404, detail="Evidence graph not found")
    return record

@router.get("/{project_id}/snapshots")
def list_project_snapshots(project_id: str, request: Request):
    project = authoritative_project(project_id, request)
    authorize_request(request, "project:read")
    enforce_project_tenant_scope(request, project)

    return list_project_snapshots_use_case(
        project_snapshot_repository(request),
        project_id,
    )


@router.get("/{project_id}/similar-cases/rag")
def similar_cases_rag(
    project_id: str,
    request: Request,
    q: str | None = None,
    snapshotId: str | None = None,
    k: int = 5,
):
    """Retrieve relevant prior reviewed cases (institutional-memory RAG) with citations."""
    project = authoritative_project(project_id, request)
    authorize_request(request, "project:read")
    enforce_project_tenant_scope(request, project)
    from use_cases.similar_case_rag import retrieve_similar_cases_rag

    corpus = _tenant_scoped_snapshot_corpus(
        project_id,
        project_snapshot_repository(request).list_all(),
    )
    return retrieve_similar_cases_rag(corpus, query=q, query_snapshot_id=snapshotId, k=max(1, min(k, 20)))

@router.post("/{project_id}/snapshots/similar")
def search_project_similar_snapshots(
    project_id: str,
    request: Request,
    input_data: Dict[str, Any] | None = None,
):
    project = authoritative_project(project_id, request)
    authorize_request(request, "project:read")
    enforce_project_tenant_scope(request, project)
    snapshot_repo = project_snapshot_repository(request)

    try:
        return search_similar_project_snapshots_use_case(
            snapshot_repo,
            project_id,
            input_data,
            candidate_snapshots=_tenant_scoped_snapshot_corpus(
                project_id,
                snapshot_repo.list_all(),
            ),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.post("/{project_id}/snapshots/diff")
def diff_project_snapshots(
    project_id: str,
    request: Request,
    input_data: Dict[str, Any] | None = None,
):
    project = authoritative_project(project_id, request)
    authorize_request(request, "project:read")
    enforce_project_tenant_scope(request, project)
    input_data = input_data or {}
    base_snapshot_id = input_data.get("baseSnapshotId") or input_data.get("base_snapshot_id")
    compare_snapshot_id = input_data.get("compareSnapshotId") or input_data.get("compare_snapshot_id")
    if not base_snapshot_id or not compare_snapshot_id:
        raise HTTPException(status_code=400, detail="baseSnapshotId and compareSnapshotId are required")

    try:
        return diff_project_snapshots_use_case(
            project_snapshot_repository(request),
            project_id,
            str(base_snapshot_id),
            str(compare_snapshot_id),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.get("/{project_id}/snapshots/{snapshot_id}")
def get_project_snapshot(project_id: str, snapshot_id: str, request: Request):
    project = authoritative_project(project_id, request)
    authorize_request(request, "project:read")
    enforce_project_tenant_scope(request, project)

    try:
        return get_project_snapshot_use_case(
            project_snapshot_repository(request),
            project_id,
            snapshot_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.post("/{project_id}/snapshots/{snapshot_id}/outcome")
def upsert_project_snapshot_outcome(
    project_id: str,
    snapshot_id: str,
    request: Request,
    input_data: Dict[str, Any],
):
    project = authoritative_project(project_id, request)
    authz = authorize_request(request, "project:write")
    enforce_project_tenant_scope(request, project)
    try:
        snapshot = upsert_project_snapshot_outcome_use_case(
            project_snapshot_repository(request),
            project_id,
            snapshot_id,
            input_data or {},
            updated_by=user_id_from_request(request),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    outcome = snapshot.get("historicalOutcome") or {}
    record_sensitive_project_event(
        request,
        event_type="historical_outcome_upserted",
        project_id=project_id,
        run_id=snapshot.get("runId"),
        metadata={
            "snapshotId": snapshot_id,
            "outcomeStatus": outcome.get("outcomeStatus"),
            "repaymentCompleted": outcome.get("repaymentCompleted"),
            "eodOccurred": outcome.get("eodOccurred"),
            "lossOccurred": outcome.get("lossOccurred"),
            "recoveryRate": outcome.get("recoveryRate"),
            "authorization": authz.get("authorization"),
        },
    )
    return {
        "schemaVersion": "realytics-historical-outcome-upsert-v1",
        "projectId": project_id,
        "snapshotId": snapshot_id,
        "runId": snapshot.get("runId"),
        "historicalOutcome": outcome,
        "snapshot": snapshot,
    }

@router.post("/{project_id}/orchestrations/{run_id}/approve")
def approve_project_orchestration(
    project_id: str,
    run_id: str,
    request: Request,
    input_data: Dict[str, Any] | None = None,
):
    project = authoritative_project(project_id, request)

    authorization = authorize_request(request, "project:review")
    enforce_project_tenant_scope(request, project)
    enforce_high_risk_step_up(
        authorization,
        action="pf_orchestration_final_approve",
    )
    actor_id = user_id_from_request(request)
    approval_input = dict(input_data or {})
    approval_input["approved_by"] = actor_id
    approval_input.pop("approvedBy", None)

    try:
        result = approve_orchestration_run_by_id_use_case(
            orchestration_repository(request),
            project_id,
            run_id,
            approval_input,
            now_iso(),
            approver_id=actor_id,
        )
        record_sensitive_project_event(
            request,
            event_type="pf_orchestration_final_approved",
            project_id=project_id,
            run_id=run_id,
            metadata={
                "authorization": authorization.get("authorization"),
                "identityAssurance": authorization.get("identityAssurance"),
            },
        )
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# Job lifecycle routes are isolated from this domain router while reusing its
# canonical project hydration and orchestration repositories.
jobs_router = build_project_jobs_router(
    project_lookup=lambda project_id, tenant_id: project_repository(
        tenant_id=tenant_id
    ).get(str(project_id)),
    source_builder=_hydrated_project_source,
    orchestration_repository_factory=(
        lambda tenant_id: orchestration_repository(tenant_id=tenant_id)
    ),
)
source_documents_router = build_project_source_documents_router(
    project_lookup=authoritative_project,
    source_document_repository_factory=project_source_document_read_repository,
    upload_archive_repository_factory=upload_archive_repository,
    audit_recorder=record_sensitive_project_event,
)
PARSER_V2_RUNTIME_DEPENDENCIES = build_parser_v2_runtime_dependencies(
    project_lookup=lambda project_id: _project_store().get(str(project_id)),
)
parser_v2_router = build_project_parser_v2_router(
    project_lookup=lambda project_id: _project_store().get(str(project_id)),
    source_document_repository_factory=source_document_repository,
    upload_archive_repository_factory=upload_archive_repository,
    audit_recorder=record_sensitive_project_event,
    **PARSER_V2_RUNTIME_DEPENDENCIES,
)

@router.post("/{project_id}/orchestrations/{run_id}/handoffs/{handoff_id}/review")
def review_project_orchestration_handoff(
    project_id: str,
    run_id: str,
    handoff_id: str,
    request: Request,
    input_data: Dict[str, Any] | None = None,
):
    project = authoritative_project(project_id, request)
    authorization = authorize_request(request, "project:review")
    enforce_project_tenant_scope(request, project)
    review_input = dict(input_data or {})
    review_input["reviewed_by"] = verified_actor_id(authorization)
    review_input.pop("reviewedBy", None)

    try:
        return review_orchestration_handoff_by_id_use_case(
            orchestration_repository(request),
            project_id,
            run_id,
            handoff_id,
            review_input,
            now_iso(),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.post("/{project_id}/orchestrations/{run_id}/risk-register/{risk_id}/review")
def review_project_orchestration_risk_register_row(
    project_id: str,
    run_id: str,
    risk_id: str,
    request: Request,
    input_data: Dict[str, Any] | None = None,
):
    project = authoritative_project(project_id, request)
    authz = authorize_request(request, "risk:review")
    enforce_project_tenant_scope(request, project)
    review_input = dict(input_data or {})
    review_input["reviewed_by"] = verified_actor_id(authz)
    review_input.pop("reviewedBy", None)

    try:
        reviewed = review_risk_register_row_by_id_use_case(
            orchestration_repository(request),
            project_id,
            run_id,
            risk_id,
            review_input,
            now_iso(),
        )
        record_sensitive_project_event(
            request,
            event_type="risk_register_row_review",
            project_id=project_id,
            run_id=run_id,
            metadata={
                "riskId": risk_id,
                "reviewStatus": review_input.get("review_status") or review_input.get("reviewStatus"),
                "authorization": authz.get("authorization"),
            },
        )
        return reviewed
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
