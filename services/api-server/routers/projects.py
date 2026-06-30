from fastapi import APIRouter, HTTPException, File, UploadFile, Header, Request, Response
from fastapi.responses import FileResponse
from typing import Dict, Any
from datetime import datetime, timezone
import base64
import tempfile
import os
from uuid import uuid4
from agents.ai_connection import resolve_ai_context
from agents.feasibility_workbook_importer import import_feasibility_study_from_workbook
from agents.feasibility_workbook_validator import validate_feasibility_workbook
from agents.parser_agent import PARSER_AGENT_VERSION, ParserAgent
from agents.parser_provenance import attach_parser_field_provenance
from agents.risk_report_workbook_importer import import_risk_report_workbook

from core.authz import authorize_request, identity_context_from_request
from core.calculations import (
    calculate_cashflow_max_minus,
    build_monthly_cash_flow,
    calculate_pf_metrics,
    calculate_feasibility_metrics,
    calculate_return_metrics,
)
from core.risk_assessment import build_basic_risk_matrix
from schemas.scenario import ScenarioSetRequest
from schemas.stress_test import StressTestRequest, StressTranche, StressScenarioDriver, StressTestSchedule
from agents.scenario_engine_agent import ScenarioEngineAgent
from agents.stress_test_agent import StressTestAgent
from schemas.credit_enhancement import CreditEnhancementRequest
from agents.credit_enhancement_agent import CreditEnhancementAgent
from schemas.cash_sweep import CashSweepRequest
from agents.cash_sweep_agent import CashSweepAgent
from integrations.kakao import build_kakao_intake_plan_message, build_kakao_workflow_result_message
from agents.restructuring_agent import RestructuringAgent
from db.repositories.market_evidence_repository import MarketEvidenceRepository
from db.repositories.orchestration_repository import OrchestrationRepository
from db.repositories.project_repository import ProjectRepository
from db.repositories.project_snapshot_repository import ProjectSnapshotRepository
from db.repositories.report_repository import ReportRepository
from db.repositories.source_document_repository import SourceDocumentRepository
from db.repositories.upload_archive_repository import UploadArchiveRepository
from storage import (
    get_evidence_graph_record,
    get_prompt_artifact_record,
    get_risk_register_record,
    list_evidence_graph_records,
    list_assumption_override_records,
    list_land_evidence_records,
    list_prompt_artifact_records,
    list_restructuring_cure_records,
    list_risk_register_records,
    load_state,
    record_user_activity_log,
    save_state,
    upsert_project_record,
    delete_project_record_cascade,
    upsert_orchestration_run,
    upsert_report_record,
    upsert_project_snapshot_record,
    upsert_assumption_override_record,
    upsert_land_evidence_record,
    upsert_prompt_artifact_record,
    upsert_restructuring_cure_record,
    upsert_risk_register_record,
)
from use_cases.cash_flow import run_cash_flow_use_case
from use_cases.common import deep_merge
from use_cases.development_feasibility import analyze_development_feasibility_use_case
from use_cases.evidence_review import list_market_evidence_use_case, review_market_evidence_use_case
from use_cases.excel_workbook_learning import learn_excel_workbook_mapping_use_case
from agents.land_ledger_agent import LandLedgerParseError
from use_cases.land_ledger_import import analyze_land_ledger_workbook_use_case, enrich_land_ledger_use_case
from use_cases.feasibility_workbook_llm import (
    apply_feasibility_input_use_case,
    extract_feasibility_fields_via_llm,
)
from use_cases.legal_review import review_legal_and_permits_use_case
from use_cases.marketability_review import analyze_marketability_use_case
from use_cases.orchestration_approval import approve_orchestration_run_by_id_use_case, review_orchestration_handoff_by_id_use_case, review_risk_register_row_by_id_use_case
from use_cases.pf_review import run_and_save_pf_review_use_case, plan_orchestration_use_case, run_workflow_use_case
from use_cases.async_jobs import submit_pf_review_job, get_job_store, recover_interrupted_jobs
from use_cases.term_annotation import annotate_terms_use_case
from use_cases.covenant_monitoring import (
    monitor_covenants_use_case,
    set_monitoring_thresholds_use_case,
)
from use_cases.portfolio_monitoring import build_portfolio_monitoring
from use_cases.map_intelligence import build_project_map_intelligence, resolve_and_attach_geometry
from integrations.vworld import build_pnu_geometry_resolver, vworld_configured
from db.parcel_geometry_cache import build_parcel_geometry_cache, build_caching_resolver
from integrations.market_data import MolitRtmsConnector
from agents.document_classifier_agent import classify_document
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
from use_cases.report_generation import export_saved_report_use_case, generate_and_save_pf_report_use_case
from use_cases.report_templates import (
    get_report_template_use_case,
    list_report_templates_use_case,
    upsert_report_template_use_case,
)
from use_cases.source_documents import (
    archive_source_document_use_case,
    delete_project_upload_archive_use_case,
    get_source_document_download_use_case,
    list_source_documents_use_case,
    save_source_document_metadata_use_case,
)
from use_cases.workbook_import_approval import (
    approve_feasibility_workbook_import_use_case,
    approve_risk_report_workbook_import_use_case,
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

def persist_state() -> None:
    save_state(
        PROJECTS_DB,
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
    payload = PROJECTS_DB.get(str(project_id))
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

def project_repository() -> ProjectRepository:
    return ProjectRepository(
        PROJECTS_DB,
        REPORTS_DB,
        ORCHESTRATIONS_DB,
        MARKET_EVIDENCE_DB,
        PROJECT_SNAPSHOTS_DB,
        persist_state,
        allocate_project_id,
        persist_project=persist_project,
    )

def report_repository() -> ReportRepository:
    return ReportRepository(REPORTS_DB, persist_state, persist_report=persist_report)

def source_document_repository() -> SourceDocumentRepository:
    return SourceDocumentRepository(PROJECTS_DB, persist_state, persist_project=persist_project)

def upload_archive_repository() -> UploadArchiveRepository:
    return UploadArchiveRepository()

def market_evidence_repository() -> MarketEvidenceRepository:
    return MarketEvidenceRepository(MARKET_EVIDENCE_DB, persist_state)

def project_snapshot_repository() -> ProjectSnapshotRepository:
    return ProjectSnapshotRepository(PROJECT_SNAPSHOTS_DB, persist_state, persist_snapshot=persist_snapshot)

def orchestration_repository() -> OrchestrationRepository:
    return OrchestrationRepository(
        ORCHESTRATIONS_DB,
        report_repository(),
        market_evidence_repository(),
        project_snapshot_repository(),
        persist_state,
        persist_run=persist_orchestration_run,
    )

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def user_id_from_request(request: Request) -> str:
    context = identity_context_from_request(request)
    user = context.get("user") or {}
    return user.get("userId") or "anonymous"

def tenant_id_from_request(request: Request) -> str | None:
    context = identity_context_from_request(request)
    user = context.get("user") or {}
    session = context.get("session") or {}
    return user.get("tenantId") or session.get("tenantId")

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
    record_user_activity_log(
        {
            "eventType": event_type,
            "userId": user_id_from_request(request),
            "projectId": project_id,
            "runId": run_id,
            "method": request.method,
            "path": request.url.path,
            "clientHost": request.client.host if request.client else None,
            "userAgent": request.headers.get("user-agent"),
            "requestId": request.headers.get("x-request-id"),
            "metadata": metadata or {},
        }
    )

def land_evidence_records_from_ledger(land_ledger: Dict[str, Any]) -> list[Dict[str, Any]]:
    records: list[Dict[str, Any]] = []
    for parcel in land_ledger.get("parcels", []) or []:
        pnu = parcel.get("pnu") or parcel.get("resolvedPnu")
        parcel_key = parcel.get("normalizedLotNumber") or parcel.get("addressFragment") or parcel.get("sequence")
        if pnu:
            records.append({
                "recordKey": f"pnu_resolution:{pnu}",
                "evidenceId": f"pnu_resolution:{pnu}",
                "evidenceType": "pnu_resolution",
                "pnu": pnu,
                "parcelKey": parcel_key,
                "addressFragment": parcel.get("addressFragment"),
                "matchConfidence": parcel.get("pnuResolveConfidence"),
                "matchMethod": parcel.get("pnuMatchMethod"),
                "sourceName": parcel.get("enrichmentEvidence", {}).get("pnuResolution", {}).get("sourceName", "LandLedgerEnrichmentAgent"),
                "sourceType": parcel.get("enrichmentEvidence", {}).get("pnuResolution", {}).get("sourceType", "manual_or_configured_connector"),
                "sourceUrlOrEvidenceId": parcel.get("enrichmentEvidence", {}).get("pnuResolution", {}).get("sourceUrlOrEvidenceId"),
                "verificationStatus": "resolved" if (parcel.get("pnuResolveConfidence") or 0) >= 0.85 else "needs_review",
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
                    "officialLandPrices": package.get("officialLandPrices", []),
                    "registryEvidence": package.get("registryEvidence", []),
                    "mode": "server_land_evidence_store",
                },
            )
        except Exception as exc:
            enriched_project["landEvidence"]["ledgerHydrationWarning"] = {
                "message": "Stored land evidence was attached as a flat package, but could not be merged into the land ledger.",
                "error": str(exc),
            }
    return enriched_project

def attach_approved_assumption_overrides(project_id: str, project_data: Dict[str, Any]) -> Dict[str, Any]:
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

def attach_active_restructuring_cures(project_id: str, project_data: Dict[str, Any]) -> Dict[str, Any]:
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
        agent = ParserAgent(ai_context)
        text = agent.parse_document(tmp_path)
        parsed_data = agent.extract_project_data(text, ai_context)
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
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    archive_meta = archive_source_document_use_case(
        upload_archive_repository(),
        project_id,
        content,
        file.filename,
        file.content_type,
    )
    upload_meta = {
        "originalFileName": file.filename,
        "originalFileHash": archive_meta["archiveId"],
        "documentType": "land_ledger",
        "contentType": file.content_type,
        "fileSizeBytes": len(content),
        "uploadedAt": uploaded_at,
        "parserVersion": "land-ledger-agent-v1",
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
            feasibility_study_v2_draft = import_feasibility_study_from_workbook(
                tmp_path,
                project_id=project_id,
                source_document=upload_meta,
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
        upload_meta["validationStatus"] = validation.get("status")
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

async def learn_uploaded_feasibility_workbook(project_id: str, file: UploadFile) -> Dict[str, Any]:
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
        return {
            "project_id": project_id,
            "upload": upload_meta,
            "learning": learning,
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
    global project_id_counter
    state = load_state()
    if state:
        PROJECTS_DB.update(state["projects"])
        REPORTS_DB.update(state["reports"])
        ORCHESTRATIONS_DB.update(state["orchestrations"])
        MARKET_EVIDENCE_DB.update(state.get("market_evidence", {}))
        PROJECT_SNAPSHOTS_DB.update(state.get("project_snapshots", {}))
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
                    PROJECTS_DB[pid] = data
                    project_id_counter += 1
        persist_state()

load_fixtures()

@router.get("/")
def get_projects(request: Request):
    projects = list_projects_use_case(project_repository())
    return [project for project in projects if project_visible_to_identity(project, request)]

@router.post("/")
def create_project(project_data: Dict[str, Any], request: Request):
    return create_project_use_case(project_repository(), stamp_project_identity(project_data, request))

@router.get("/{project_id}")
def get_project(project_id: str):
    project = get_project_use_case(project_repository(), project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project

@router.put("/{project_id}")
def update_project(project_id: str, project_data: Dict[str, Any]):
    project = update_project_use_case(project_repository(), project_id, project_data)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project

@router.delete("/{project_id}")
def delete_project(project_id: str):
    deleted = delete_project_use_case(project_repository(), project_id)
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
    file: UploadFile = File(...),
    x_realytics_user_id: str | None = Header(default=None),
    x_realytics_ai_provider: str | None = Header(default=None),
):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")

    ai_context = resolve_ai_context(
        {
            "user_id": x_realytics_user_id,
            "provider": x_realytics_ai_provider,
            "host": "api",
        },
    )
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
    return project

async def _classify_intake_file(project_id: str, file: UploadFile) -> Dict[str, Any]:
    """Sniff + classify one uploaded file, archive the original, return the plan."""
    content = await file.read()
    name = file.filename or "upload"
    ctype = file.content_type
    ext = os.path.splitext(name)[1].lower()
    sheet_names = None
    json_keys = None
    if ext in (".xlsx", ".xlsm"):
        try:
            import io
            import openpyxl  # type: ignore

            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
            sheet_names = list(wb.sheetnames)
            wb.close()
        except Exception:
            pass
    elif ext == ".json":
        try:
            data = json.loads(content.decode("utf-8"))
            if isinstance(data, dict):
                json_keys = list(data.keys())
        except Exception:
            pass

    classification = classify_document(
        name, content_type=ctype, size=len(content), sheet_names=sheet_names, json_keys=json_keys
    )
    try:
        archive = upload_archive_repository().archive_upload(project_id, content, name, ctype)
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
        classification["archiveStored"] = False
        classification["archiveError"] = type(exc).__name__
    return classification


@router.post("/{project_id}/intake/classify")
async def smart_intake_classify(project_id: str, files: list[UploadFile] = File(...)):
    """스마트 업로드: 여러 자료를 한 번에 받아 파서 에이전트가 문서 유형을 자동
    분류하고 원본을 보관한다(사업계획서/토지대장/사업수지/토지증빙/시장자료 등)."""
    if project_id not in PROJECTS_DB:
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


@router.post("/{project_id}/intake/triage")
def smart_intake_triage(project_id: str, input_data: Dict[str, Any] | None = None):
    """Recommend the OS business track after SmartIntake classification.

    The recommendation is based only on classified document metadata. It never
    treats the uploaded material as reviewed/approved evidence.
    """
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    payload = input_data or {}
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    result = {
        "projectId": project_id,
        "persisted": True,
        "confidenceBasis": "문서유형별 업무트랙 가중치 × 자동분류 신뢰도 + 파일명 신호를 기준으로 산정합니다. 승인 가능성은 별도 Approval Readiness Gate에서 판단합니다.",
        **triage_intake_documents(files, ai_context=payload.get("aiContext")),
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
    project = dict(PROJECTS_DB[project_id])
    history = list(project.get("intakeTriageHistory") or [])
    history.append(history_item)
    project["intakeTriageHistory"] = history[-20:]
    project["latestIntakeTriage"] = history_item
    PROJECTS_DB[project_id] = project
    persist_state()
    record_user_activity_log(
        {
            "eventType": "intake_triage_completed",
            "userId": "web",
            "projectId": project_id,
            "metadata": {
                "recommendedTrack": result.get("recommendedTrack"),
                "recommendationSource": result.get("recommendationSource"),
                "aiAssisted": result.get("aiAssisted"),
                "fileCount": len(files),
            },
        }
    )
    return result


@router.post("/{project_id}/land-ledger/analyze")
async def analyze_land_ledger_upload(
    project_id: str,
    file: UploadFile = File(...),
):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    ledger = await parse_uploaded_land_ledger(project_id, file)
    upload_meta = ledger.get("upload")
    if isinstance(upload_meta, dict):
        save_source_document_metadata_use_case(source_document_repository(), project_id, upload_meta)
    return {
        "project_id": project_id,
        "landLedger": ledger,
        "note": "PNU and official land price enrichment require external connectors after ledger import.",
    }

@router.post("/{project_id}/feasibility-workbook/validate")
async def validate_project_feasibility_workbook(
    project_id: str,
    file: UploadFile = File(...),
):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    result = await validate_uploaded_feasibility_workbook(project_id, file)
    save_source_document_metadata_use_case(source_document_repository(), project_id, result["upload"])
    return result


@router.post("/{project_id}/feasibility-workbook/extract")
async def extract_project_feasibility_workbook(
    project_id: str,
    file: UploadFile = File(...),
    x_realytics_user_id: str | None = Header(default=None),
    x_realytics_ai_provider: str | None = Header(default=None),
):
    """LLM-extract financial figures from an arbitrary 사업수지 workbook (any layout).

    Returns a reviewable draft with per-field source-cell evidence + detected unit.
    Does NOT persist — the reviewer applies via /feasibility-workbook/apply-input.
    """
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    suffix = os.path.splitext(file.filename or "")[1] or ".xlsx"
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail={"error": "empty_file", "message": "빈 파일입니다."})
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    archive_meta = archive_source_document_use_case(
        upload_archive_repository(), project_id, content, file.filename, file.content_type
    )
    upload_meta = {
        "originalFileName": file.filename,
        "originalFileHash": archive_meta["archiveId"],
        "documentType": "feasibility_workbook",
        "contentType": file.content_type,
        "fileSizeBytes": len(content),
        "uploadedAt": now_iso(),
        "parserVersion": "feasibility-workbook-llm-extract-v1",
        **archive_meta,
    }
    save_source_document_metadata_use_case(source_document_repository(), project_id, upload_meta)
    ai_context = resolve_ai_context(
        {"user_id": x_realytics_user_id, "provider": x_realytics_ai_provider, "host": "api"}
    )
    try:
        draft = extract_feasibility_fields_via_llm(tmp_path, ai_context)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return {"project_id": project_id, "upload": upload_meta, **draft}


@router.post("/{project_id}/feasibility-workbook/apply-input")
def apply_project_feasibility_input(project_id: str, request: Request, input_data: Dict[str, Any]):
    """Persist a reviewer-confirmed feasibility_input (from the LLM extract draft)."""
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authz = authorize_request(request, "assumption:override")
    feasibility_input = input_data.get("feasibility_input") or input_data.get("feasibilityInput") or {}
    try:
        result = apply_feasibility_input_use_case(
            project_repository(),
            project_id,
            feasibility_input,
            applied_by=user_id_from_request(request),
            source_document=input_data.get("sourceDocument"),
        )
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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authz = authorize_request(request, "assumption:override")
    try:
        result = approve_feasibility_workbook_import_use_case(
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

@router.post("/{project_id}/feasibility-workbook/learn")
async def learn_project_feasibility_workbook(
    project_id: str,
    file: UploadFile = File(...),
):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    result = await learn_uploaded_feasibility_workbook(project_id, file)
    save_source_document_metadata_use_case(source_document_repository(), project_id, result["upload"])
    return result

@router.post("/{project_id}/risk-report-workbook/validate")
async def validate_project_risk_report_workbook(
    project_id: str,
    file: UploadFile = File(...),
):
    if project_id not in PROJECTS_DB:
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
    if project_id not in PROJECTS_DB:
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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")

    input_data = input_data or {}
    land_ledger = input_data.get("landLedger") or PROJECTS_DB[project_id].get("landLedger")
    if not land_ledger:
        raise HTTPException(status_code=400, detail="landLedger payload or saved project landLedger is required")

    enriched = enrich_land_ledger_use_case(land_ledger, input_data.get("options", {}))
    if input_data.get("persist", False):
        project = dict(PROJECTS_DB[project_id])
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
        "note": "PNU/official price/registry enrichment uses provided connector records or legalDongCode; no official price or registry status is fabricated without source evidence.",
    }

@router.post("/{project_id}/upload-and-run")
async def upload_business_plan_and_run_pf_review(
    project_id: str,
    file: UploadFile = File(...),
    x_realytics_user_id: str | None = Header(default=None),
    x_realytics_ai_provider: str | None = Header(default=None),
):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")

    ai_context = resolve_ai_context(
        {
            "user_id": x_realytics_user_id or "web",
            "provider": x_realytics_ai_provider,
            "host": "api",
        },
    )
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
        orchestration_repository(),
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
def resolve_project_ai_connection(project_id: str, input_data: Dict[str, Any] | None = None):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    input_data = input_data or {}
    return {
        "project_id": project_id,
        "ai_connection": resolve_ai_context(
            input_data.get("ai_context", input_data),
            user_id=input_data.get("user_id"),
            host=input_data.get("host") or "api",
        ),
        "note": (
            "Claude Desktop hosts MCP tool calls. Direct Claude extraction inside the API "
            "requires ANTHROPIC_API_KEY."
        ),
    }

@router.post("/{project_id}/feasibility/calculate")
def calculate_feasibility(project_id: str, input_data: Dict[str, Any]):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    project_feasibility = PROJECTS_DB[project_id].get("feasibility_input", {})
    source_data = deep_merge(project_feasibility, input_data or {})
    return calculate_feasibility_metrics(source_data)

@router.post("/{project_id}/development-feasibility/analyze")
def analyze_development_feasibility(project_id: str, input_data: Dict[str, Any] | None = None):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    return analyze_development_feasibility_use_case(PROJECTS_DB[project_id], input_data)

@router.post("/{project_id}/cashflow/calculate")
def calculate_cashflow(project_id: str, input_data: Dict[str, Any]):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    cfs = input_data.get("cashflows", [])
    max_minus = calculate_cashflow_max_minus(cfs)
    result = build_monthly_cash_flow(input_data)
    result["cashflows"] = cfs
    result["legacy_max_minus"] = max_minus
    return result

@router.post("/{project_id}/cash-flow/run")
def run_project_cash_flow(project_id: str, input_data: Dict[str, Any] | None = None):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    return run_cash_flow_use_case(PROJECTS_DB[project_id], input_data)

@router.post("/{project_id}/pf/structure")
def calculate_pf_structure(project_id: str, input_data: Dict[str, Any]):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    project = PROJECTS_DB[project_id]
    feasibility = project.get("feasibility_input", {})
    source_data = {
        "tranches": input_data.get("tranches", project.get("pf_structure", [])),
        "total_revenue": input_data.get("total_revenue", feasibility.get("total_revenue", 0)),
        "total_cost": input_data.get("total_cost", feasibility.get("total_cost", 0)),
        "finance_cost": input_data.get("finance_cost", feasibility.get("finance_cost", 0)),
        "other_priority_costs": input_data.get("other_priority_costs", input_data.get("finance_cost", feasibility.get("finance_cost", 0))),
        "net_operating_income": input_data.get("net_operating_income", input_data.get("noi", 0)),
        "debt_service": input_data.get("debt_service", 0),
    }
    result = calculate_pf_metrics(source_data)
    return {
        **result,
        "ltv": result["metrics"]["ltv"]["value"],
        "ltc": result["metrics"]["ltc"]["value"],
        "dscr": result["metrics"]["dscr"]["value"],
        "exit_sale_rate": result["metrics"]["exit_sale_rate"]["value"],
    }

@router.post("/{project_id}/returns/calculate")
def calculate_returns(project_id: str, input_data: Dict[str, Any] | None = None):
    """Project IRR, Equity IRR, Equity Multiple, Payback, and ICR.

    Reuses the project's feasibility inputs (for ICR's operating income and
    interest) and accepts an explicit ``project_cashflows`` / ``equity_cashflows``
    series or monthly ``revenue_schedule`` / ``cost_schedule`` overrides.
    """
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    feasibility = PROJECTS_DB[project_id].get("feasibility_input", {})
    source_data = deep_merge(feasibility, input_data or {})
    return calculate_return_metrics(source_data)

@router.post("/{project_id}/risk/assess")
def assess_risk(project_id: str, input_data: Dict[str, Any]):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    input_data = input_data or {}
    source_data = deep_merge(PROJECTS_DB[project_id], input_data.get("project_data", {}))
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
    # Fallback if empty
    if not product_mix:
        feas_in = project.get("feasibility_input", {})
        total_rev = float(feas_in.get("total_revenue") or feas_in.get("sales_revenue") or 100000000)
        product_mix.append({
            "type": "General",
            "units": 100,
            "avgUnitPrice": total_rev / 100.0,
            "totalRevenue": total_rev
        })

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
                "priority": int(item.get("priority") or idx + 1),
                "interestRate": float(item.get("interestRate") or item.get("interest_rate") or item.get("rate") or 0.0),
                "feeRate": float(item.get("feeRate") or item.get("fee_rate") or 0.0)
            })
    # Fallback if empty
    if not pf_structure:
        pf_structure.append({
            "tranche": "Senior",
            "amount": float(project.get("pf_amount") or 10000000),
            "priority": 1,
            "interestRate": 0.065,
            "feeRate": 0.01
        })

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
        tranches.append(StressTranche(
            tranche=str(tranche.get("tranche") or tranche.get("name") or f"Tr.{index}"),
            amount=_to_number(tranche.get("amount", 0.0)),
            priority=int(tranche.get("priority", index) or index),
            interestRate=_to_number(tranche.get("interestRate", tranche.get("interest_rate", tranche.get("rate", 0.0)))),
            feeRate=_to_number(tranche.get("feeRate", tranche.get("fee_rate", 0.0))),
        ))

    scenarios = None
    if isinstance(input_data.get("scenarios"), list) and input_data["scenarios"]:
        scenarios = [StressScenarioDriver(**s) for s in input_data["scenarios"]]
    schedule = StressTestSchedule(**input_data["schedule"]) if isinstance(input_data.get("schedule"), dict) else None

    return StressTestRequest(
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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    req = build_stress_test_request_from_project(PROJECTS_DB[project_id], input_data or {})
    result = StressTestAgent().run_stress_test(req)
    return result.model_dump()


@router.post("/{project_id}/credit-enhancement/evaluate")
def evaluate_credit_enhancement(project_id: str, input_data: Dict[str, Any] | None = None):
    """Evaluate the six PF credit-enhancement measures and quantify their
    combined effect on senior LTV, credit grade, and DSCR."""
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    input_data = input_data or {}
    if input_data.get("baseSeniorLtv") in (None, ""):
        # Default the base senior LTV from the project's PF structure metrics.
        project = PROJECTS_DB[project_id]
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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    req = CashSweepRequest(**(input_data or {}))
    return CashSweepAgent().simulate(req).model_dump()


@router.post("/{project_id}/scenarios/waterfall")
def run_project_scenarios_waterfall(project_id: str, input_data: Dict[str, Any]):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    req = build_scenario_request_from_project(PROJECTS_DB[project_id], input_data)
    agent = ScenarioEngineAgent()
    result = agent.run_scenario_set(req)
    return {
        "scenarioSetId": result.scenarioSetId,
        "waterfallScenarios": result.waterfallScenarios
    }


@router.post("/{project_id}/scenarios/unsold-ltv-matrix")
def run_project_scenarios_unsold_ltv(project_id: str, input_data: Dict[str, Any]):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    req = build_scenario_request_from_project(PROJECTS_DB[project_id], input_data)
    agent = ScenarioEngineAgent()
    result = agent.run_scenario_set(req)
    return {
        "scenarioSetId": result.scenarioSetId,
        "unsoldLtvMatrix": result.unsoldLtvMatrix
    }


@router.post("/{project_id}/scenarios/recovery")
def run_project_scenarios_recovery(project_id: str, input_data: Dict[str, Any]):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")

    project = PROJECTS_DB[project_id]
    req = build_scenario_request_from_project(project, input_data)
    agent = ScenarioEngineAgent()
    result = agent.run_scenario_set(req)

    # Persist in the project record
    project_copy = dict(project)
    project_copy.setdefault("scenario_results", {})
    project_copy["scenario_results"][result.scenarioSetId] = result.model_dump()
    PROJECTS_DB[project_id] = project_copy
    persist_state()

    return result

@router.post("/{project_id}/scenarios/restructuring")
def run_project_scenarios_restructuring(project_id: str, input_data: Dict[str, Any] | None = None):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")

    input_data = input_data or {}
    project = PROJECTS_DB[project_id]
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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    source = PROJECTS_DB[project_id]
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
    return analyze_marketability_use_case(source, input_data)

@router.get("/{project_id}/source-documents")
def list_project_source_documents(project_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    return list_source_documents_use_case(source_document_repository(), project_id)

@router.get("/{project_id}/land-evidence")
def list_project_land_evidence(project_id: str, request: Request, pnu: str | None = None, evidence_type: str | None = None):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "evidence:read")
    return {
        "schemaVersion": "realytics-land-evidence-list-v1",
        "projectId": project_id,
        "records": list_land_evidence_records(project_id, pnu=pnu, evidence_type=evidence_type),
    }

@router.post("/{project_id}/land-evidence")
def upsert_project_land_evidence(project_id: str, request: Request, input_data: Dict[str, Any]):
    if project_id not in PROJECTS_DB:
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
    }

@router.get("/{project_id}/assumption-overrides")
def list_project_assumption_overrides(
    project_id: str,
    request: Request,
    status: str | None = None,
    check_key: str | None = None,
):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    return {
        "schemaVersion": "realytics-assumption-override-list-v1",
        "projectId": project_id,
        "records": list_assumption_override_records(project_id, status=status, check_key=check_key),
    }

@router.post("/{project_id}/assumption-overrides")
def upsert_project_assumption_override(project_id: str, request: Request, input_data: Dict[str, Any]):
    if project_id not in PROJECTS_DB:
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

@router.get("/{project_id}/restructuring-cures")
def list_project_restructuring_cures(
    project_id: str,
    request: Request,
    status: str | None = None,
    cure_type: str | None = None,
):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    return {
        "schemaVersion": "realytics-restructuring-cure-list-v1",
        "projectId": project_id,
        "records": list_restructuring_cure_records(project_id, status=status, cure_type=cure_type),
    }

@router.post("/{project_id}/restructuring-cures")
def upsert_project_restructuring_cure(project_id: str, request: Request, input_data: Dict[str, Any]):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authz = authorize_request(request, "project:write")
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

@router.get("/{project_id}/source-documents/{archive_id}/download")
def download_project_source_document(project_id: str, archive_id: str, request: Request):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authz = authorize_request(request, "evidence:read")
    download = get_source_document_download_use_case(
        source_document_repository(),
        upload_archive_repository(),
        project_id,
        archive_id,
    )
    if not download:
        raise HTTPException(status_code=404, detail="Source document not found")

    record_sensitive_project_event(
        request,
        event_type="source_document_download",
        project_id=project_id,
        metadata={
            "archiveId": archive_id,
            "filename": download.get("filename"),
            "mediaType": download.get("mediaType"),
            "fileSizeBytes": download.get("fileSizeBytes"),
            "archiveIntegrityStatus": download.get("archiveIntegrityStatus"),
            "authorization": authz.get("authorization"),
        },
    )

    return FileResponse(
        download["path"],
        media_type=download["mediaType"],
        filename=download["filename"],
    )

@router.get("/{project_id}/market-evidence")
def list_project_market_evidence(project_id: str, run_id: str | None = None):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    return list_market_evidence_use_case(market_evidence_repository(), project_id, run_id)

@router.post("/{project_id}/market-evidence/review")
def review_project_market_evidence(project_id: str, input_data: Dict[str, Any]):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        return review_market_evidence_use_case(market_evidence_repository(), input_data, now_iso(), project_id)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.post("/{project_id}/legal/review")
def review_legal_and_permits(project_id: str, input_data: Dict[str, Any] | None = None):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    return review_legal_and_permits_use_case(PROJECTS_DB[project_id], input_data)

@router.post("/{project_id}/reports/generate")
def generate_report(project_id: str, input_data: Dict[str, Any]):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")

    result = generate_and_save_pf_report_use_case(report_repository(), project_id, PROJECTS_DB[project_id], input_data)
    structured_report = result["generated"]["structuredReport"]
    md_report = result["generated"]["reportMarkdown"]
    report_record = result["reportRecord"]
    return {
        "project_id": project_id,
        "report_id": report_record["report_id"],
        "version": report_record["version"],
        "template": structured_report["template"],
        "report": structured_report,
        "report_markdown": md_report,
        "metadata": report_record["metadata"],
    }

@router.get("/{project_id}/reports")
def get_reports(project_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    return report_repository().list_metadata(project_id)

@router.get("/{project_id}/reports/{report_id}")
def get_report(project_id: str, report_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    repo = report_repository()
    record = repo.get(project_id, report_id)
    if not record:
        raise HTTPException(status_code=404, detail="Report not found")
    return {
        **repo.metadata(record),
        "report": record.get("report"),
        "report_markdown": record.get("report_markdown"),
        "exports": [repo.export_metadata(export) for export in record.get("exports", [])],
    }

@router.get("/{project_id}/report-templates")
def list_report_templates(project_id: str):
    try:
        return list_report_templates_use_case(project_repository(), project_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.post("/{project_id}/report-templates")
def upsert_report_template(project_id: str, request: Request, input_data: Dict[str, Any]):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authz = authorize_request(request, "report:export")
    try:
        result = upsert_report_template_use_case(
            project_repository(),
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
def get_report_template(project_id: str, template_id: str):
    try:
        return get_report_template_use_case(project_repository(), project_id, template_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.post("/{project_id}/reports/{report_id}/exports")
def create_report_export(project_id: str, report_id: str, request: Request, input_data: Dict[str, Any] | None = None):
    authz = authorize_request(request, "report:export")
    try:
        export_record = export_saved_report_use_case(report_repository(), project_id, report_id, input_data)
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
    return report_repository().export_metadata(export_record)

@router.get("/{project_id}/reports/{report_id}/exports")
def list_report_exports(project_id: str, report_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    repo = report_repository()
    record = repo.get(project_id, report_id)
    if not record:
        raise HTTPException(status_code=404, detail="Report not found")
    return [repo.export_metadata(export) for export in record.get("exports", [])]

@router.get("/{project_id}/reports/{report_id}/exports/{export_id}/download")
def download_report_export(project_id: str, report_id: str, export_id: str, request: Request):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authz = authorize_request(request, "report:export")
    repo = report_repository()
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

def _hydrated_project_source(project_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
    source_data = deep_merge(PROJECTS_DB[project_id], (input_data or {}).get("project_data", {}))
    source_data = attach_persisted_land_evidence(project_id, source_data)
    source_data = attach_approved_assumption_overrides(project_id, source_data)
    source_data = attach_active_restructuring_cures(project_id, source_data)
    return source_data


@router.post("/{project_id}/orchestration/plan")
def plan_orchestration(project_id: str, request: Request, input_data: Dict[str, Any] | None = None):
    """Intent router: inspect available materials and return the workflow menu,
    missing data, and a recommended default before any agents run."""
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    source_data = _hydrated_project_source(project_id, input_data or {})
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


@router.get("/{project_id}/prompt-artifacts")
def list_project_prompt_artifacts(project_id: str, request: Request, artifact_type: str | None = None, workflow: str | None = None):
    if project_id not in PROJECTS_DB:
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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    record = get_prompt_artifact_record(project_id, artifact_id)
    if not record:
        raise HTTPException(status_code=404, detail="Prompt artifact not found")
    return record


@router.post("/{project_id}/prompt-artifacts")
def upsert_project_prompt_artifact(project_id: str, request: Request, input_data: Dict[str, Any]):
    if project_id not in PROJECTS_DB:
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
    return (PROJECTS_DB.get(project_id, {}).get("project", {}) or {}).get("name") or project_id


@router.get("/{project_id}/developability-score")
def get_project_developability_score(project_id: str):
    """Composite developability score (개발가능성 점수화) from land/regulation/feasibility/market."""
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    source_data = _hydrated_project_source(project_id, {})
    return score_developability_use_case(source_data)


@router.get("/{project_id}/monitoring")
def get_project_monitoring(project_id: str):
    """Live PF covenant monitoring (DSCR/LTV/LTC/Exit/sales-rate vs thresholds + stress EOD signal)."""
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    source_data = _hydrated_project_source(project_id, {})
    return monitor_covenants_use_case(source_data)


@router.get("/{project_id}/map")
def get_project_map_intelligence(project_id: str, resolve_geometry: bool = False):
    """Map Intelligence: parcel GeoJSON + the deck's 6-layer spatial availability.

    Deterministic and non-fabricating — parcels with no resolved geometry are
    returned as unlocated GeoJSON features and flagged. With
    `resolve_geometry=true` and a configured VWorld key, parcel boundaries are
    fetched live from the VWorld cadastral layer by PNU (capped, best-effort).
    """
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    source_data = _hydrated_project_source(project_id, {})
    geometry_resolution: Dict[str, Any] | None = None
    if resolve_geometry and vworld_configured():
        cache = build_parcel_geometry_cache()
        resolver = build_caching_resolver(cache, build_pnu_geometry_resolver(), project_id=project_id)
        resolved = resolve_and_attach_geometry(source_data, resolver, limit=30)
        geometry_resolution = {
            "source": "vworld_cadastral",
            "cacheBackend": cache.backend,
            "resolvedCount": resolved,
            "cacheHits": resolver.stats["cacheHits"],
            "fetchedFromVworld": resolver.stats["fetched"],
        }
    payload = build_project_map_intelligence(source_data)
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
    if project_id not in PROJECTS_DB:
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
    for snapshot in project_snapshot_repository().list_all():
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
    for project_id in list(PROJECTS_DB.keys()):
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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    source_data = _hydrated_project_source(project_id, input_data or {})
    return annotate_terms_use_case(source_data, project_id=project_id)


@router.post("/{project_id}/kakao/intake-plan")
def kakao_intake_plan(project_id: str, input_data: Dict[str, Any] | None = None):
    """KakaoTalk intake → intent menu. Runs the Orchestration Planner and returns
    both the plan and a KakaoTalk-safe message presenting the workflow choices."""
    if project_id not in PROJECTS_DB:
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
def run_orchestration_workflow(project_id: str, input_data: Dict[str, Any] | None = None):
    """Run only the agents required by a user-selected workflow key.

    When ``channel == 'kakao'`` the response also carries a KakaoTalk-safe
    completion message so the intake flow can notify the user of the result.
    """
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    input_data = input_data or {}
    workflow = input_data.get("workflow")
    if not workflow:
        raise HTTPException(status_code=422, detail="Missing 'workflow' selection")
    source_data = _hydrated_project_source(project_id, input_data)
    if workflow == "full_pf_review":
        run = run_and_save_pf_review_use_case(
            orchestration_repository(), project_id, source_data, input_data.get("options", {}),
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
def start_pf_review_orchestration(project_id: str, input_data: Dict[str, Any] | None = None):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")

    input_data = input_data or {}
    source_data = _hydrated_project_source(project_id, input_data)
    return run_and_save_pf_review_use_case(
        orchestration_repository(),
        project_id,
        source_data,
        input_data.get("options", {}),
    )


@router.post("/{project_id}/orchestrations/pf-review/async")
def start_pf_review_orchestration_async(project_id: str, input_data: Dict[str, Any] | None = None):
    """Enqueue a PF review run and return a job immediately (non-blocking).

    Poll `GET /projects/{id}/jobs/{job_id}` for status/result. In-process MVP
    queue — see use_cases/async_jobs.py.
    """
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    input_data = input_data or {}
    source_data = _hydrated_project_source(project_id, input_data)
    job = submit_pf_review_job(
        orchestration_repository(),
        project_id,
        source_data,
        input_data.get("options", {}),
        background=True,
    )
    return {"job": job}


@router.get("/{project_id}/jobs")
def list_project_jobs(project_id: str, limit: int = 50):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"jobs": get_job_store().list(project_id=project_id, limit=limit)}


@router.get("/{project_id}/jobs/{job_id}")
def get_project_job(project_id: str, job_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    job = get_job_store().get(job_id)
    if job is None or job.get("projectId") != project_id:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _resubmit_pf_review_job(old_job: Dict[str, Any]) -> Dict[str, Any] | None:
    """Rebuild project_data from a persisted job and re-enqueue it (recovery)."""
    project_id = old_job.get("projectId")
    if project_id not in PROJECTS_DB:
        return None
    payload = (old_job.get("params") or {}).get("inputData") or {}
    source_data = _hydrated_project_source(project_id, payload)
    return submit_pf_review_job(
        orchestration_repository(),
        project_id,
        source_data,
        payload.get("options", {}),
        background=True,
        resume_payload=payload,
        recovered_from=old_job.get("jobId"),
    )


@router.post("/{project_id}/jobs/recover")
def recover_project_jobs(project_id: str, request: Request):
    """Recover this project's interrupted jobs (queued/running left by a restart)."""
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    return recover_interrupted_jobs(_resubmit_pf_review_job, project_id=project_id)


@router.get("/{project_id}/orchestrations")
def list_project_orchestrations(project_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")

    return orchestration_repository().list_metadata(project_id)

@router.get("/{project_id}/orchestrations/{run_id}")
def get_project_orchestration(project_id: str, run_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")

    run = orchestration_repository().get(project_id, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Orchestration run not found")
    return run


@router.get("/{project_id}/orchestrations/{run_id}/credit-documents")
def get_project_orchestration_credit_documents(project_id: str, run_id: str, request: Request):
    """Return the Term Sheet and IC Memo generated during a PF review run."""
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")

    run = orchestration_repository().get(project_id, run_id)
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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authz = authorize_request(request, "report:export")

    artifact_key = CREDIT_DOCUMENT_ARTIFACT_KEYS.get(document)
    if not artifact_key:
        raise HTTPException(status_code=400, detail="document must be 'term-sheet' or 'ic-memo'")
    export_format = format.lower().strip()
    if export_format not in CREDIT_DOCUMENT_EXPORT_FORMATS:
        raise HTTPException(status_code=400, detail="format must be markdown, pdf, or docx")
    if export_format == "md":
        export_format = "markdown"

    run = orchestration_repository().get(project_id, run_id)
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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    record = get_risk_register_record(project_id, run_id)
    if not record:
        run = orchestration_repository().get(project_id, run_id)
        record = (run or {}).get("artifacts", {}).get("riskRegister")
    if not record:
        raise HTTPException(status_code=404, detail="Risk register not found")
    return record

@router.get("/{project_id}/evidence-graphs")
def list_project_evidence_graphs(project_id: str, request: Request):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    record = get_evidence_graph_record(project_id, run_id)
    if not record:
        run = orchestration_repository().get(project_id, run_id)
        record = (run or {}).get("artifacts", {}).get("evidenceGraph")
    if not record:
        raise HTTPException(status_code=404, detail="Evidence graph not found")
    return record

@router.get("/{project_id}/snapshots")
def list_project_snapshots(project_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")

    return list_project_snapshots_use_case(project_snapshot_repository(), project_id)


@router.get("/{project_id}/similar-cases/rag")
def similar_cases_rag(
    project_id: str,
    request: Request,
    q: str | None = None,
    snapshotId: str | None = None,
    k: int = 5,
):
    """Retrieve relevant prior reviewed cases (institutional-memory RAG) with citations."""
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    from use_cases.similar_case_rag import retrieve_similar_cases_rag

    corpus = project_snapshot_repository().list_all()
    return retrieve_similar_cases_rag(corpus, query=q, query_snapshot_id=snapshotId, k=max(1, min(k, 20)))

@router.post("/{project_id}/snapshots/similar")
def search_project_similar_snapshots(project_id: str, input_data: Dict[str, Any] | None = None):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        return search_similar_project_snapshots_use_case(
            project_snapshot_repository(),
            project_id,
            input_data,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.post("/{project_id}/snapshots/diff")
def diff_project_snapshots(project_id: str, input_data: Dict[str, Any] | None = None):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    input_data = input_data or {}
    base_snapshot_id = input_data.get("baseSnapshotId") or input_data.get("base_snapshot_id")
    compare_snapshot_id = input_data.get("compareSnapshotId") or input_data.get("compare_snapshot_id")
    if not base_snapshot_id or not compare_snapshot_id:
        raise HTTPException(status_code=400, detail="baseSnapshotId and compareSnapshotId are required")

    try:
        return diff_project_snapshots_use_case(
            project_snapshot_repository(),
            project_id,
            str(base_snapshot_id),
            str(compare_snapshot_id),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.get("/{project_id}/snapshots/{snapshot_id}")
def get_project_snapshot(project_id: str, snapshot_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        return get_project_snapshot_use_case(project_snapshot_repository(), project_id, snapshot_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.post("/{project_id}/snapshots/{snapshot_id}/outcome")
def upsert_project_snapshot_outcome(
    project_id: str,
    snapshot_id: str,
    request: Request,
    input_data: Dict[str, Any],
):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authz = authorize_request(request, "project:write")
    try:
        snapshot = upsert_project_snapshot_outcome_use_case(
            project_snapshot_repository(),
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
def approve_project_orchestration(project_id: str, run_id: str, input_data: Dict[str, Any] | None = None):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        return approve_orchestration_run_by_id_use_case(orchestration_repository(), project_id, run_id, input_data, now_iso())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.post("/{project_id}/orchestrations/{run_id}/handoffs/{handoff_id}/review")
def review_project_orchestration_handoff(
    project_id: str,
    run_id: str,
    handoff_id: str,
    input_data: Dict[str, Any] | None = None,
):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        return review_orchestration_handoff_by_id_use_case(
            orchestration_repository(),
            project_id,
            run_id,
            handoff_id,
            input_data,
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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authz = authorize_request(request, "risk:review")

    try:
        reviewed = review_risk_register_row_by_id_use_case(
            orchestration_repository(),
            project_id,
            run_id,
            risk_id,
            input_data,
            now_iso(),
        )
        record_sensitive_project_event(
            request,
            event_type="risk_register_row_review",
            project_id=project_id,
            run_id=run_id,
            metadata={
                "riskId": risk_id,
                "reviewStatus": (input_data or {}).get("review_status") or (input_data or {}).get("reviewStatus"),
                "authorization": authz.get("authorization"),
            },
        )
        return reviewed
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
