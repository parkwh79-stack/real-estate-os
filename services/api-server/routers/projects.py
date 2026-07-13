from fastapi import APIRouter, HTTPException, File, UploadFile, Header, Request, Response, Query
from fastapi.responses import FileResponse
from typing import Dict, Any
from datetime import datetime, timezone
import base64
import tempfile
import os
import re
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
    upsert_project_snapshot_record,
    upsert_assumption_override_record,
    upsert_land_evidence_record,
    upsert_chief_pod_artifact_record,
    upsert_pod_ai_review_record,
    upsert_recalculation_event_record,
    upsert_prompt_artifact_record,
    upsert_restructuring_cure_record,
    upsert_risk_register_record,
    upsert_calculation_run_record,
)
from use_cases.cash_flow import run_cash_flow_use_case
from use_cases.chief_deal_orchestration import (
    plan_chief_deal_orchestration_use_case,
    run_chief_deal_all_pods_use_case,
    run_chief_deal_pod_use_case,
)
from use_cases.common import deep_merge
from use_cases.development_feasibility import analyze_development_feasibility_use_case
from use_cases.asset_feasibility_comparison import compare_asset_feasibility_use_case
from use_cases.covenant_eod import evaluate_covenant_eod_use_case
from use_cases.product_planning import compare_product_planning_use_case
from use_cases.market_product_depth import analyze_market_product_depth_use_case
from use_cases.reanalyze_on_evidence import reanalyze_after_evidence_use_case
from use_cases.evidence_review import list_market_evidence_use_case, review_market_evidence_use_case
from use_cases.excel_workbook_learning import learn_excel_workbook_mapping_use_case
from agents.land_ledger_agent import LandLedgerParseError
from use_cases.land_ledger_import import analyze_land_ledger_workbook_use_case, enrich_land_ledger_use_case
from use_cases.feasibility_workbook_llm import (
    apply_feasibility_input_use_case,
    extract_feasibility_fields_via_llm,
)
from use_cases.intake_finalize import finalize_project_intake_use_case
from use_cases.parcel_pnu_resolution import resolve_project_parcels_use_case, resolve_address_to_parcel
from use_cases.auto_evidence_supplement import auto_supplement_validation_gaps
from use_cases.parsed_facts import parsed_fact_package, persist_parsed_facts
from use_cases.parsed_fact_validation import validate_parsed_facts_for_pod
from use_cases.calculation_run_capture import scenario_engine_run_payloads, stress_test_run_payload
from use_cases.legal_review import review_legal_and_permits_use_case
from use_cases.marketability_review import analyze_marketability_use_case
from use_cases.orchestration_approval import approve_orchestration_run_by_id_use_case, review_orchestration_handoff_by_id_use_case, review_risk_register_row_by_id_use_case
from use_cases.pf_review import run_and_save_pf_review_use_case, plan_orchestration_use_case, run_workflow_use_case
from use_cases.async_jobs import submit_pf_review_job, get_job_store, recover_interrupted_jobs
from use_cases.term_annotation import annotate_terms_use_case
from use_cases.user_ai_profile import build_user_ai_context, safe_user_ai_context_summary
from use_cases.covenant_monitoring import (
    monitor_covenants_use_case,
    set_monitoring_thresholds_use_case,
)
from use_cases.portfolio_monitoring import build_portfolio_monitoring
from use_cases.map_intelligence import build_project_map_intelligence, resolve_and_attach_geometry
from integrations.vworld import build_pnu_geometry_resolver, vworld_configured, vworld_status
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
from use_cases.source_locator_completeness import run_source_locator_completeness_audit_use_case
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
    if explicit_provider:
        override["provider"] = explicit_provider
    user_id = user.get("userId") or explicit_user_id or "anonymous"
    tenant_id = user.get("tenantId") or session.get("tenantId")
    return build_user_ai_context(
        user_id=user_id,
        tenant_id=tenant_id,
        host="api",
        override=override,
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

def land_ledger_auto_enrichment_options(project_id: str, land_ledger: Dict[str, Any], options: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Build best-effort, source-backed enrichment options for an uploaded land ledger.

    The function is intentionally conservative. It never fabricates PNU, price,
    or registry evidence. It only adds:
    - legalDongCode derived from an already stored project PNU; or
    - pnuResolutions resolved through VWorld address -> parcel lookup.
    Official land price/cadastral live adapters remain governed by
    LandDataConnectorRouter config and explicit options.
    """
    merged = dict(options or {})
    project = PROJECTS_DB.get(project_id) or {}
    project_pnu = str(((project.get("land") or {}).get("pnu") or "")).strip()
    if not merged.get("legalDongCode") and len(project_pnu) >= 10 and project_pnu[:10].isdigit():
        merged["legalDongCode"] = project_pnu[:10]
        merged.setdefault("pnuResolverSource", "project_land_pnu_legal_dong_code")

    if "autoResolvePnuFromAddress" not in merged and "auto_resolve_pnu_from_address" not in merged:
        # When a confirmed legal dong code is already available, deterministic
        # legal-dong + lot-number PNU generation is safer and faster than a
        # best-effort address geocode. Use VWorld address lookup by default
        # only when we need to discover the legal dong/PNU from project address.
        merged["autoResolvePnuFromAddress"] = not bool(merged.get("legalDongCode") or merged.get("legal_dong_code"))

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
        if parcel.get("pnu") or parcel.get("resolvedPnu"):
            continue
        address = _parcel_lookup_address(parcel, project_base_address)
        if not address:
            diagnostics.append({
                "connector": "vworld_address_pnu",
                "status": "skipped_missing_address",
                "sequence": parcel.get("sequence"),
            })
            continue

        resolved = resolve_address_to_parcel(address)
        if resolved.get("status") == "resolved" and resolved.get("pnu"):
            key = str(resolved["pnu"])
            if key not in seen_keys:
                pnu_resolutions.append({
                    "parcelKey": parcel.get("normalizedLotNumber") or parcel.get("addressFragment") or parcel.get("sequence"),
                    "sequence": parcel.get("sequence"),
                    "addressFragment": address,
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
                    "parcelKey": parcel.get("normalizedLotNumber") or parcel.get("addressFragment") or parcel.get("sequence"),
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
                "parcelKey": parcel.get("normalizedLotNumber") or parcel.get("addressFragment") or parcel.get("sequence"),
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
                "status": resolved.get("status") or "unresolved",
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


def _parcel_lookup_address(parcel: Dict[str, Any], project_base_address: str | None) -> str | None:
    raw = str(
        parcel.get("addressFragment")
        or parcel.get("normalizedLotNumber")
        or parcel.get("rawLotNumber")
        or ""
    ).strip()
    if not raw:
        return None
    if any("\uac00" <= char <= "\ud7a3" for char in raw):
        return raw
    lot = raw
    if (parcel.get("isMountain") or parcel.get("is_mountain")) and not lot.startswith("산"):
        lot = f"산 {lot}"
    if project_base_address:
        return f"{project_base_address} {lot}".strip()
    return lot

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
    scenario = input_data.get("scenario") or input_data.get("scenarioContext") or input_data.get("scenario_context")
    if not scenario and input_data.get("scenarioId"):
        scenario = {"scenarioId": input_data.get("scenarioId")}
    return upsert_calculation_run_record(
        project_id,
        {
            "engineKey": engine_key,
            "engineVersion": input_data.get("engineVersion") or f"{engine_key}-v1",
            "inputEvidenceIds": input_data.get("inputEvidenceIds")
            or input_data.get("evidenceIds")
            or ([input_data.get("sourceDocumentId")] if input_data.get("sourceDocumentId") else []),
            "assumptionIds": input_data.get("assumptionIds") or [],
            "outputMetrics": _metric_rows_from_result(result, prefix=f"{engine_key}." ) + _monthly_cashflow_metric_rows(result, engine_key),
            "riskIssues": _risk_rows_from_result(result),
            "resultSnapshot": result,
            "scenario": scenario or {},
            "artifactRef": artifact_ref or {},
            "sourceLocator": source_locator,
            "inputDigest": {
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
) -> Dict[str, str]:
    """Persist Waterfall / Unsold LTV / Tranche Recovery runs from one scenario
    set. Returns {engineKey: calculationRunId}. Failures never break the API
    response — the scenario result itself is still returned."""
    run_ids: Dict[str, str] = {}
    try:
        payloads = scenario_engine_run_payloads(scenario_dump, engines=engines)
    except Exception:
        return run_ids
    for payload in payloads:
        try:
            run = _persist_calculation_run(
                project_id,
                engine_key=payload["engineKey"],
                result=payload["result"],
                input_data={**(input_data or {}), "engineVersion": payload["engineVersion"]},
                artifact_ref=payload["artifactRef"],
            )
            run_ids[payload["engineKey"]] = str(run.get("calculationRunId"))
        except Exception:
            continue
    return run_ids


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
    request: Request,
    file: UploadFile = File(...),
    x_realytics_user_id: str | None = Header(default=None),
    x_realytics_ai_provider: str | None = Header(default=None),
):
    if project_id not in PROJECTS_DB:
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
    if project_id not in PROJECTS_DB or not _completed_pod_keys(project_id):
        return None
    try:
        return _reanalyze_after_evidence(project_id, changed_document_types=doc_types)
    except Exception:
        return None


def _maybe_auto_reanalyze_on_upload(project_id: str, parsed_data: Dict[str, Any] | None) -> Dict[str, Any] | None:
    meta = (parsed_data or {}).get("_meta") or {}
    upload_meta = meta.get("upload") if isinstance(meta.get("upload"), dict) else {}
    doc_type = upload_meta.get("documentType") or meta.get("documentType") or "business_plan"
    return _maybe_auto_reanalyze(project_id, [str(doc_type)])

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
def smart_intake_triage(project_id: str, request: Request, input_data: Dict[str, Any] | None = None):
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
        **triage_intake_documents(
            files,
            ai_context={
                **ai_context_from_request(request, payload_ai_context=payload.get("aiContext")),
                "project_id": project_id,
                "agent_id": "IntakeTriageAgent",
            },
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
    project = dict(PROJECTS_DB[project_id])
    history = list(project.get("intakeTriageHistory") or [])
    history.append(history_item)
    project["intakeTriageHistory"] = history[-20:]
    project["latestIntakeTriage"] = history_item
    PROJECTS_DB[project_id] = project
    persist_project(project_id)
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


@router.post("/{project_id}/intake/finalize")
def finalize_smart_intake(project_id: str, input_data: Dict[str, Any] | None = None):
    """After classify+parse: promote identity, resolve address->PNU, and return
    a step-by-step readiness plan.

    PF review does not run by default. The user must explicitly trigger a review
    after source documents, land evidence, market evidence, feasibility inputs,
    and risk/cure decisions are sufficiently prepared.
    """
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    payload = input_data or {}
    track = str(payload.get("track") or "")
    run = payload.get("run", False)

    finalize = finalize_project_intake_use_case(project_repository(), project_id, track=track)

    analysis_run = None
    if run is True and track in {"development", "pf"}:
        source_data = _hydrated_project_source(project_id, {})
        run_result = run_and_save_pf_review_use_case(
            orchestration_repository(), project_id, source_data, {}
        )
        analysis_run = {
            "run_id": run_result.get("run_id"),
            "status": run_result.get("status"),
            "workflow": run_result.get("workflow"),
        }

    return {**finalize, "track": track, "analysisRun": analysis_run}


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

    # Persist the parsed ledger onto the project so parcels are actually usable
    # downstream (batch PNU resolution, land pod, map) — previously the analysis
    # was returned to the caller and dropped.
    ledger_payload = {k: v for k, v in ledger.items() if k != "upload"}
    update_payload: Dict[str, Any] = {"landLedger": ledger_payload}
    parcels = ledger_payload.get("parcels") or []
    if parcels and not (PROJECTS_DB[project_id].get("land_acquisition") or []):
        update_payload["land_acquisition"] = parcels
    project_repository().update(project_id, update_payload)

    auto = _maybe_auto_reanalyze(project_id, ["land_ledger"])
    return {
        "project_id": project_id,
        "landLedger": ledger,
        "persisted": True,
        "autoReanalysis": auto,
        "note": "필지가 프로젝트에 저장되었습니다. /land/parcels/resolve로 지번별 PNU·공시지가를 일괄 해석할 수 있습니다.",
    }


@router.post("/{project_id}/land/parcels/resolve")
def resolve_project_parcel_pnus(project_id: str, request: Request, input_data: Dict[str, Any] | None = None):
    """토지조서의 모든 필지에 대해 지번별 PNU·공시지가를 VWorld로 일괄 해석·저장."""
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:write")
    payload = input_data or {}
    try:
        limit = max(1, min(500, int(payload.get("limit") or 150)))
    except (TypeError, ValueError):
        limit = 150
    result = resolve_project_parcels_use_case(project_repository(), project_id, limit=limit)
    project_after = PROJECTS_DB.get(project_id) or {}
    parcels: list[Dict[str, Any]] = []
    land_ledger = project_after.get("landLedger") or project_after.get("land_ledger")
    if isinstance(land_ledger, dict) and isinstance(land_ledger.get("parcels"), list):
        parcels.extend([parcel for parcel in land_ledger["parcels"] if isinstance(parcel, dict)])
    if isinstance(project_after.get("land_acquisition"), list):
        seen_keys = {
            str(parcel.get("pnu") or parcel.get("resolvedPnu") or parcel.get("normalizedLotNumber") or parcel.get("addressFragment") or parcel.get("sequence"))
            for parcel in parcels
        }
        for parcel in project_after["land_acquisition"]:
            if not isinstance(parcel, dict):
                continue
            key = str(parcel.get("pnu") or parcel.get("resolvedPnu") or parcel.get("normalizedLotNumber") or parcel.get("addressFragment") or parcel.get("sequence"))
            if key in seen_keys:
                continue
            parcels.append(parcel)
            seen_keys.add(key)
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
        "autoReanalysis": _maybe_auto_reanalyze(project_id, ["land_ledger"]),
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
    request: Request,
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
    ai_context = ai_context_from_request(
        request,
        explicit_provider=x_realytics_ai_provider,
        explicit_user_id=x_realytics_user_id,
    )
    ai_context = {**ai_context, "project_id": project_id, "agent_id": "FeasibilityWorkbookLLMAgent"}
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
        "vworld": vworld_status(),
        "note": "PNU is resolved from VWorld address/parcel lookup when VWORLD_API_KEY is configured, or generated only from confirmed legalDongCode plus normalized lot fields. Official price/registry status is never fabricated without source evidence.",
    }

@router.post("/{project_id}/upload-and-run")
async def upload_business_plan_and_run_pf_review(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
    x_realytics_user_id: str | None = Header(default=None),
    x_realytics_ai_provider: str | None = Header(default=None),
):
    if project_id not in PROJECTS_DB:
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
def resolve_project_ai_connection(project_id: str, request: Request, input_data: Dict[str, Any] | None = None):
    if project_id not in PROJECTS_DB:
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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    project_feasibility = PROJECTS_DB[project_id].get("feasibility_input", {})
    source_data = deep_merge(project_feasibility, input_data or {})
    result = calculate_feasibility_metrics(source_data)
    run = _persist_calculation_run(project_id, engine_key="feasibility", result=result, input_data=input_data)
    return {**result, "calculationRunId": run["calculationRunId"]}


@router.get("/{project_id}/calculation-runs")
def list_project_calculation_runs(
    project_id: str,
    request: Request,
    engine_key: str | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    records = list_calculation_run_records(project_id, engine_key=engine_key, limit=limit)
    return {
        "schemaVersion": "realytics-calculation-run-list-v2",
        "projectId": project_id,
        "engineKey": engine_key,
        "count": len(records),
        "records": records,
    }


@router.get("/{project_id}/deal-state-records")
def list_project_deal_state_records(
    project_id: str,
    request: Request,
    record_type: str | None = None,
    calculation_run_id: str | None = None,
    review_status: str | None = None,
    missing_locator: bool | None = None,
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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    records = list_deal_state_records(
        project_id,
        record_type=record_type,
        calculation_run_id=calculation_run_id,
        review_status=review_status,
        missing_locator=missing_locator,
        limit=limit,
    )
    # Completeness summary over the same record-type scope, independent of the
    # locator/review filters, so the badge total does not shift as the reviewer
    # narrows the view.
    scope = list_deal_state_records(
        project_id,
        record_type=record_type,
        calculation_run_id=calculation_run_id,
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
    if project_id not in PROJECTS_DB:
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
    if project_id not in PROJECTS_DB:
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
        project_data=PROJECTS_DB[project_id],
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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:review")
    review_status = str(payload.get("reviewStatus") or payload.get("review_status") or "").strip()
    if review_status not in {"approved", "waived", "needs_revision", "resolved"}:
        raise HTTPException(status_code=400, detail="reviewStatus must be approved, waived, needs_revision, or resolved")
    records = list_deal_state_records(project_id, limit=1000)
    record = next((item for item in records if item.get("recordId") == record_id), None)
    if not record:
        raise HTTPException(status_code=404, detail="Deal State record not found")

    reviewer = str(payload.get("reviewedBy") or payload.get("reviewed_by") or identity_context_from_request(request).get("userId") or "reviewer")
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
    record_user_activity_log(
        {
            "eventType": "deal_state_record_reviewed",
            "projectId": project_id,
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
    if project_id not in PROJECTS_DB:
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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    payload = input_data or {}
    result = analyze_development_feasibility_use_case(PROJECTS_DB[project_id], payload)
    run = _persist_calculation_run(
        project_id,
        engine_key="feasibility",
        result=result,
        input_data={
            **payload,
            "engineVersion": "development_feasibility_agent_v2",
            "sourceLocator": payload.get("sourceLocator") or {
                "sourceType": "project_feasibility_input",
                "projectId": project_id,
            },
        },
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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    payload = input_data or {}
    scenarios = payload.get("scenarios") if isinstance(payload.get("scenarios"), list) else None
    options = payload.get("options") if isinstance(payload.get("options"), dict) else None
    return compare_asset_feasibility_use_case(PROJECTS_DB[project_id], scenarios, options)


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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    payload = input_data or {}
    overrides = payload.get("overrides") if isinstance(payload.get("overrides"), dict) else None
    policy = payload.get("policy") if isinstance(payload.get("policy"), dict) else None
    return evaluate_covenant_eod_use_case(PROJECTS_DB[project_id], overrides, policy)


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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    payload = input_data or {}
    scenarios = payload.get("scenarios") if isinstance(payload.get("scenarios"), list) else None
    options = payload.get("options") if isinstance(payload.get("options"), dict) else None
    return compare_product_planning_use_case(PROJECTS_DB[project_id], scenarios, options)


@router.post("/{project_id}/market-product-depth/analyze")
def analyze_market_product_depth(
    project_id: str,
    request: Request,
    input_data: Dict[str, Any] | None = None,
):
    """Market-Product depth: 제공주체(시행사/매각주관사/공공/AI) 분류·비교, 상품유형별
    분양성 Score, 흡수율 모델, AI 추가필요작업 판단. Deterministic numbers; the LLM
    only judges what additional evidence/analysis is needed (fallback deterministic)."""
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    payload = input_data or {}
    options = payload.get("options") if isinstance(payload.get("options"), dict) else None
    return analyze_market_product_depth_use_case(PROJECTS_DB[project_id], options)

@router.post("/{project_id}/cashflow/calculate")
def calculate_cashflow(project_id: str, input_data: Dict[str, Any]):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    cfs = input_data.get("cashflows", [])
    max_minus = calculate_cashflow_max_minus(cfs)
    result = build_monthly_cash_flow(input_data)
    result["cashflows"] = cfs
    result["legacy_max_minus"] = max_minus
    run = _persist_calculation_run(project_id, engine_key="cashflow", result=result, input_data=input_data)
    return {**result, "calculationRunId": run["calculationRunId"]}

@router.post("/{project_id}/cash-flow/run")
def run_project_cash_flow(project_id: str, input_data: Dict[str, Any] | None = None):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    result = run_cash_flow_use_case(PROJECTS_DB[project_id], input_data)
    run = _persist_calculation_run(project_id, engine_key="cash_flow_engine", result=result, input_data=input_data)
    return {**result, "calculationRunId": run["calculationRunId"]}

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
    response = {
        **result,
        "ltv": result["metrics"]["ltv"]["value"],
        "ltc": result["metrics"]["ltc"]["value"],
        "dscr": result["metrics"]["dscr"]["value"],
        "exit_sale_rate": result["metrics"]["exit_sale_rate"]["value"],
    }
    pf_run = _persist_calculation_run(project_id, engine_key="pf_metrics", result=response, input_data=input_data)
    ltv_run = _persist_calculation_run(
        project_id,
        engine_key="ltv_ltc_dscr",
        result=response,
        input_data={**(input_data or {}), "engineVersion": "ltv_ltc_dscr_v1"},
        artifact_ref={"type": "pf_structure", "section": "metrics", "parentCalculationRunId": pf_run["calculationRunId"]},
    )
    exit_run = _persist_calculation_run(
        project_id,
        engine_key="exit_sale_rate",
        result=response,
        input_data={**(input_data or {}), "engineVersion": "exit_sale_rate_v1"},
        artifact_ref={"type": "pf_structure", "section": "exit_sale_rate", "parentCalculationRunId": pf_run["calculationRunId"]},
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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    feasibility = PROJECTS_DB[project_id].get("feasibility_input", {})
    source_data = deep_merge(feasibility, input_data or {})
    result = calculate_return_metrics(source_data)
    run = _persist_calculation_run(project_id, engine_key="return_metrics", result=result, input_data=input_data)
    return {**result, "calculationRunId": run["calculationRunId"]}

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
    dump = result.model_dump()
    # Persist as a first-class CalculationRun (covers the discount-stress stages);
    # STRESS_SENIOR_SHORTFALL / UNSOLD_LTV_BREACH flags become auditable riskIssues.
    payload = stress_test_run_payload(dump)
    run = _persist_calculation_run(
        project_id,
        engine_key=payload["engineKey"],
        result=payload["result"],
        input_data={**(input_data or {}), "engineVersion": payload["engineVersion"]},
        artifact_ref=payload["artifactRef"],
    )
    dump["calculationRunId"] = run.get("calculationRunId")
    return dump


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
    run_id = _persist_scenario_calculation_runs(project_id, result.model_dump(), input_data, engines=["waterfall"]).get("waterfall")
    return {
        "scenarioSetId": result.scenarioSetId,
        "waterfallScenarios": result.waterfallScenarios,
        "calculationRunId": run_id,
    }


@router.post("/{project_id}/scenarios/unsold-ltv-matrix")
def run_project_scenarios_unsold_ltv(project_id: str, input_data: Dict[str, Any]):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    req = build_scenario_request_from_project(PROJECTS_DB[project_id], input_data)
    agent = ScenarioEngineAgent()
    result = agent.run_scenario_set(req)
    run_id = _persist_scenario_calculation_runs(project_id, result.model_dump(), input_data, engines=["unsold_ltv"]).get("unsold_ltv")
    return {
        "scenarioSetId": result.scenarioSetId,
        "unsoldLtvMatrix": result.unsoldLtvMatrix,
        "calculationRunId": run_id,
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
    dump = result.model_dump()
    project_copy["scenario_results"][result.scenarioSetId] = dump
    PROJECTS_DB[project_id] = project_copy
    persist_project(project_id)

    # Full scenario set = all three engines get their own CalculationRun so the
    # SENIOR_SHORTFALL / UNSOLD_LTV_BREACH history is queryable per engine.
    run_ids = _persist_scenario_calculation_runs(project_id, dump, input_data)
    dump["calculationRunIds"] = run_ids
    return dump

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
    result = analyze_marketability_use_case(source, input_data)
    if (input_data or {}).get("persist"):
        project = dict(PROJECTS_DB[project_id])
        project["marketability"] = result
        project["marketabilityAnalysis"] = result
        if live_comparables and isinstance(source, dict) and source.get("market"):
            project["market"] = source.get("market")
        updated = update_project_use_case(project_repository(), project_id, project)
        if not updated:
            raise HTTPException(status_code=404, detail="Project not found")
    return result

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
        "autoReanalysis": _maybe_auto_reanalyze(project_id, ["land_evidence"]),
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
        "paragraphEvidenceLinks": structured_report.get("paragraphEvidenceLinks"),
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
    source_data = attach_persisted_parsed_facts(project_id, source_data)
    source_data = attach_approved_assumption_overrides(project_id, source_data)
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
    pnu_count = sum(1 for parcel in parcels if isinstance(parcel, dict) and (parcel.get("pnu") or parcel.get("resolvedPnu")))
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
    project_data = PROJECTS_DB.get(project_id)
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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authz = authorize_request(request, "project:write")
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

    project_data = attach_approved_assumption_overrides(project_id, PROJECTS_DB[project_id])
    project_data = attach_active_restructuring_cures(project_id, project_data)
    project_data = dict(project_data)
    project_data["adoptedRestructuringCures"] = [saved_cure]
    rerun = run_and_save_pf_review_use_case(
        orchestration_repository(),
        project_id,
        project_data,
        {"trigger": "unsold_ltv_cure", "cureId": saved_cure.get("cureId")},
    )
    PROJECTS_DB[project_id] = project_data
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
    plan: Dict[str, Any],
    source_documents: list[Dict[str, Any]],
    land_evidence_records: list[Dict[str, Any]],
    parsed_fact_package_data: Dict[str, Any],
    assumption_overrides: list[Dict[str, Any]],
    chief_artifacts: list[Dict[str, Any]],
) -> Dict[str, Any]:
    normalized_page = _normalize_workspace_page(page)
    land_ledger = source_data.get("landLedger") or source_data.get("land_ledger")
    common = {
        "sourceDocumentSummary": _summarize_source_documents(source_documents),
        "landLedgerSummary": _summarize_land_ledger(source_data),
        "landEvidenceSummary": _summarize_land_evidence(land_evidence_records),
        "parsedFactSummary": parsed_fact_package_data.get("summary") or {},
        "parsedFactCount": parsed_fact_package_data.get("recordCount") or 0,
        "assumptionOverrideCount": len(assumption_overrides),
        "chiefArtifactSummary": _summarize_chief_artifacts(chief_artifacts),
    }

    if normalized_page == "input_package":
        return {
            **common,
            "sourceDocuments": source_documents,
            "parsedFacts": parsed_fact_package_data.get("records") or [],
            "landEvidenceRecords": land_evidence_records,
            "assumptionOverrides": assumption_overrides,
            "missingInputRegister": (plan.get("sharedDealStateGraph") or {}).get("missingInputRegister") or [],
            "recommendedActions": [
                "업로드 문서를 자동분류한 뒤 프로젝트 DB에 원본과 메타데이터를 저장합니다.",
                "토지조서가 있으면 PNU, 공시지가, 등기/제한물권 증빙 보강을 먼저 실행합니다.",
                "시장·분양성 근거와 사업수지/CF가 준비된 뒤 PF-Credit Pod를 실행합니다.",
            ],
        }

    if normalized_page in {"land", "map"}:
        return {
            **common,
            "land": source_data.get("land") or {},
            "landLedger": land_ledger or {},
            "landEvidenceRecords": land_evidence_records,
            "pnuValidation": {
                "required": True,
                "ready": not common["landLedgerSummary"]["needsPnuEnrichment"],
                "pnuCount": common["landLedgerSummary"]["pnuCount"],
                "parcelCount": common["landLedgerSummary"]["parcelCount"],
            },
            "mapDataEndpoint": f"/projects/{project_id}/map?resolve_geometry=true&geometry_limit=150",
        }

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
            "landEvidenceRecords": land_evidence_records,
            "evidenceGraphEndpoint": f"/projects/{project_id}/evidence-graphs",
            "evidenceGraphDetailEndpointTemplate": f"/projects/{project_id}/orchestrations/{{run_id}}/evidence-graph",
        }

    return {
        **common,
        "agentPods": plan.get("agentPods") or [],
        "podRuns": plan.get("podRuns") or [],
        "taskGraph": plan.get("taskGraph") or {},
        "chiefArtifacts": chief_artifacts,
    }


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
    if project_id not in PROJECTS_DB:
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
    if project_id not in PROJECTS_DB:
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


@router.post("/{project_id}/chief-orchestration/plan")
def plan_chief_deal_orchestration(project_id: str, request: Request, input_data: Dict[str, Any] | None = None):
    """Chief Deal Orchestrator: build the AI Deal Room plan.

    This endpoint does not run pod agents. It creates a Shared Deal State Graph,
    Missing Input Register, Agent Pod plan, scenario placeholders, and a
    preliminary decision gate so the user can see what the orchestrator will do
    before any approval-sensitive analysis is treated as final.
    """
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    payload = input_data or {}
    source_data = _hydrated_project_source(project_id, payload)
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


def _persist_chief_recalculation_event(project_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
    persisted = upsert_recalculation_event_record(project_id, event)
    record_user_activity_log(
        {
            "eventType": "chief_recalculation_triggered",
            "projectId": project_id,
            "runId": persisted.get("runId"),
            "metadata": {
                "eventId": persisted.get("eventId"),
                "trigger": persisted.get("trigger"),
                "sourcePod": persisted.get("sourcePod"),
                "targetPods": persisted.get("targetPods"),
                "status": persisted.get("status"),
            },
        }
    )
    return persisted


@router.post("/{project_id}/chief-orchestration/run-pod")
def run_chief_deal_pod(project_id: str, request: Request, input_data: Dict[str, Any] | None = None):
    """Chief Deal Orchestrator: execute a single agent pod and trigger downstream events."""
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:write")
    payload = input_data or {}
    pod_key = payload.get("podKey")
    if not pod_key:
        raise HTTPException(status_code=400, detail="podKey is required")
    options = payload.get("options") or {}
    workflow_track = payload.get("workflowTrack") or payload.get("workflow_track") or payload.get("track")
    if workflow_track:
        options = {**options, "workflowTrack": workflow_track}
    payload_ai_context = (
        options.get("ai_context")
        or options.get("aiContext")
        or payload.get("ai_context")
        or payload.get("aiContext")
    )
    options = {
        **options,
        "ai_context": ai_context_from_request(request, payload_ai_context=payload_ai_context),
    }

    project_data = PROJECTS_DB[project_id]
    try:
        plan = run_chief_deal_pod_use_case(
            project_id,
            pod_key,
            project_data,
            options,
            artifact_sink=lambda record: upsert_chief_pod_artifact_record(project_id, record),
            review_sink=lambda record: upsert_pod_ai_review_record(project_id, record),
            event_sink=lambda event: _persist_chief_recalculation_event(project_id, event),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    actor = str(identity_context_from_request(request).get("userId") or "chief_deal_orchestrator")
    plan["parsedFactValidation"] = _validate_supplement_and_revalidate_parsed_facts(
        project_id,
        str(pod_key),
        project_data=project_data,
        source_artifact_key=_latest_pod_artifact_key_from_plan(plan, str(pod_key)),
        actor=actor,
    )
    _persist_shared_deal_state_graph(project_id, plan, source=f"chief_pod:{pod_key}")
    upsert_project_record(project_id, project_data)

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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:write")
    payload = input_data or {}
    options = payload.get("options") or {}
    workflow_track = payload.get("workflowTrack") or payload.get("workflow_track") or payload.get("track")
    if workflow_track:
        options = {**options, "workflowTrack": workflow_track}
    payload_ai_context = (
        options.get("ai_context")
        or options.get("aiContext")
        or payload.get("ai_context")
        or payload.get("aiContext")
    )
    options = {
        **options,
        "ai_context": ai_context_from_request(request, payload_ai_context=payload_ai_context),
    }

    project_data = PROJECTS_DB[project_id]
    plan = run_chief_deal_all_pods_use_case(
        project_id,
        project_data,
        options,
        artifact_sink=lambda record: upsert_chief_pod_artifact_record(project_id, record),
        review_sink=lambda record: upsert_pod_ai_review_record(project_id, record),
        event_sink=lambda event: _persist_chief_recalculation_event(project_id, event),
    )

    completed_artifact_keys = _completed_pod_artifact_keys_from_plan(plan)
    actor = str(identity_context_from_request(request).get("userId") or "chief_deal_orchestrator")
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
    upsert_project_record(project_id, project_data)

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
    """Pods that have a persisted artifact = already completed at least once."""
    keys = set()
    for record in list_chief_pod_artifact_records(project_id):
        pod_key = record.get("podKey") or record.get("pod_key")
        if pod_key:
            keys.add(str(pod_key))
    return keys


def _reanalyze_after_evidence(project_id: str, *, changed_document_types: list[str] | None, options: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Re-run still-open + document-affected pods with the standard persistence sinks."""
    project_data = PROJECTS_DB[project_id]
    summary = reanalyze_after_evidence_use_case(
        project_id,
        project_data,
        completed_pod_keys=_completed_pod_keys(project_id),
        changed_document_types=changed_document_types,
        options=options or {},
        artifact_sink=lambda record: upsert_chief_pod_artifact_record(project_id, record),
        review_sink=lambda record: upsert_pod_ai_review_record(project_id, record),
        event_sink=lambda event: _persist_chief_recalculation_event(project_id, event),
    )
    if summary.get("triggered"):
        upsert_project_record(project_id, PROJECTS_DB[project_id])
    return summary


@router.post("/{project_id}/chief-orchestration/reanalyze")
def reanalyze_chief_deal(project_id: str, request: Request, input_data: Dict[str, Any] | None = None):
    """Smart re-analysis after new evidence: re-run the blocked / not-yet-completed
    pods plus the pods the new document type feeds. Each pod re-run re-validates
    parsed facts, auto-supplements, regenerates the AI review, and cascades. Pods
    that still lack evidence stay blocked honestly."""
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:write")
    payload = input_data or {}
    doc_types = payload.get("changedDocumentTypes")
    doc_types = doc_types if isinstance(doc_types, list) else None
    options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
    summary = _reanalyze_after_evidence(project_id, changed_document_types=doc_types, options=options)
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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    state = PROJECTS_DB[project_id].get("chief_orchestration_state") or {}
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
    if project_id not in PROJECTS_DB:
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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    record = get_pod_ai_review_record(project_id, review_id)
    if not record:
        raise HTTPException(status_code=404, detail="Pod AI review not found")
    return record


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
    if project_id not in PROJECTS_DB:
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


@router.get("/{project_id}/chief-orchestration/events")
def list_chief_deal_events(project_id: str, request: Request, limit: int = 100):
    """Event Bus history: recalculation triggers + learning-loop events for audit.

    Combines the in-project orchestration state (fast, current) with the durable
    activity log (queryable across restarts/state rebuilds)."""
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    limit = max(1, min(500, limit))

    state = PROJECTS_DB[project_id].get("chief_orchestration_state") or {}
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
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    authorize_request(request, "project:read")
    record = get_chief_pod_artifact_record(project_id, artifact_key)
    if not record:
        record = ((PROJECTS_DB[project_id].get("chief_orchestration_state") or {}).get("artifacts") or {}).get(artifact_key)
    if not record:
        raise HTTPException(status_code=404, detail="Chief pod artifact not found")
    return record


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
def get_project_map_intelligence(
    project_id: str,
    resolve_geometry: bool = False,
    geometry_limit: int = Query(30, ge=0, le=300),
):
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
        resolved = resolve_and_attach_geometry(source_data, resolver, limit=geometry_limit)
        geometry_resolution = {
            "source": "vworld_cadastral",
            "cacheBackend": cache.backend,
            "requestedLimit": geometry_limit,
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
