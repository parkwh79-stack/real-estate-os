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
from agents.risk_report_workbook_importer import import_risk_report_workbook

from core.authz import authorize_request
from core.calculations import (
    calculate_cashflow_max_minus,
    build_monthly_cash_flow,
    calculate_pf_metrics,
    calculate_feasibility_metrics,
)
from core.risk_assessment import build_basic_risk_matrix
from schemas.scenario import ScenarioSetRequest
from agents.scenario_engine_agent import ScenarioEngineAgent
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
    get_risk_register_record,
    list_evidence_graph_records,
    list_assumption_override_records,
    list_land_evidence_records,
    list_restructuring_cure_records,
    list_risk_register_records,
    load_state,
    record_user_activity_log,
    save_state,
    upsert_assumption_override_record,
    upsert_land_evidence_record,
    upsert_restructuring_cure_record,
    upsert_risk_register_record,
)
from use_cases.cash_flow import run_cash_flow_use_case
from use_cases.common import deep_merge
from use_cases.development_feasibility import analyze_development_feasibility_use_case
from use_cases.evidence_review import list_market_evidence_use_case, review_market_evidence_use_case
from use_cases.excel_workbook_learning import learn_excel_workbook_mapping_use_case
from use_cases.land_ledger_import import analyze_land_ledger_workbook_use_case, enrich_land_ledger_use_case
from use_cases.legal_review import review_legal_and_permits_use_case
from use_cases.marketability_review import analyze_marketability_use_case
from use_cases.orchestration_approval import approve_orchestration_run_by_id_use_case, review_orchestration_handoff_by_id_use_case, review_risk_register_row_by_id_use_case
from use_cases.pf_review import run_and_save_pf_review_use_case
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

def project_repository() -> ProjectRepository:
    return ProjectRepository(
        PROJECTS_DB,
        REPORTS_DB,
        ORCHESTRATIONS_DB,
        MARKET_EVIDENCE_DB,
        PROJECT_SNAPSHOTS_DB,
        persist_state,
        allocate_project_id,
    )

def report_repository() -> ReportRepository:
    return ReportRepository(REPORTS_DB, persist_state)

def source_document_repository() -> SourceDocumentRepository:
    return SourceDocumentRepository(PROJECTS_DB, persist_state)

def upload_archive_repository() -> UploadArchiveRepository:
    return UploadArchiveRepository()

def market_evidence_repository() -> MarketEvidenceRepository:
    return MarketEvidenceRepository(MARKET_EVIDENCE_DB, persist_state)

def project_snapshot_repository() -> ProjectSnapshotRepository:
    return ProjectSnapshotRepository(PROJECT_SNAPSHOTS_DB, persist_state)

def orchestration_repository() -> OrchestrationRepository:
    return OrchestrationRepository(
        ORCHESTRATIONS_DB,
        report_repository(),
        market_evidence_repository(),
        project_snapshot_repository(),
        persist_state,
    )

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def user_id_from_request(request: Request) -> str:
    return (
        request.headers.get("x-realytics-user-id")
        or request.headers.get("x-user-id")
        or request.headers.get("x-codex-user-id")
        or request.query_params.get("user_id")
        or "anonymous"
    )

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

        return parsed_data
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

async def parse_uploaded_land_ledger(project_id: str, file: UploadFile) -> Dict[str, Any]:
    suffix = os.path.splitext(file.filename or "")[1] or ".xls"
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
def get_projects():
    return list_projects_use_case(project_repository())

@router.post("/")
def create_project(project_data: Dict[str, Any]):
    return create_project_use_case(project_repository(), project_data)

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
def analyze_marketability(project_id: str, input_data: Dict[str, Any] | None = None):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    return analyze_marketability_use_case(PROJECTS_DB[project_id], input_data)

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

@router.post("/{project_id}/orchestrations/pf-review")
def start_pf_review_orchestration(project_id: str, input_data: Dict[str, Any] | None = None):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")

    input_data = input_data or {}
    source_data = deep_merge(PROJECTS_DB[project_id], input_data.get("project_data", {}))
    source_data = attach_persisted_land_evidence(project_id, source_data)
    source_data = attach_approved_assumption_overrides(project_id, source_data)
    source_data = attach_active_restructuring_cures(project_id, source_data)
    return run_and_save_pf_review_use_case(
        orchestration_repository(),
        project_id,
        source_data,
        input_data.get("options", {}),
    )

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
