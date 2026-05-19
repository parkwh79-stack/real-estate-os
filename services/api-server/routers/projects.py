from fastapi import APIRouter, HTTPException, File, UploadFile, Header, Response
from fastapi.responses import FileResponse
from typing import Dict, Any
from datetime import datetime, timezone
import tempfile
import os
import shutil
import hashlib
import string
from pathlib import Path
from uuid import uuid4
from agents.ai_connection import resolve_ai_context
from agents.parser_agent import PARSER_AGENT_VERSION, ParserAgent

from core.calculations import (
    calculate_cashflow_max_minus,
    build_monthly_cash_flow,
    calculate_pf_metrics,
    calculate_feasibility_metrics,
)
from core.risk_assessment import build_basic_risk_matrix
from db.repositories.market_evidence_repository import MarketEvidenceRepository
from db.repositories.orchestration_repository import OrchestrationRepository
from db.repositories.project_repository import ProjectRepository
from db.repositories.report_repository import ReportRepository
from db.repositories.source_document_repository import SourceDocumentRepository
from storage import load_state, save_state
from use_cases.cash_flow import run_cash_flow_use_case
from use_cases.common import deep_merge
from use_cases.development_feasibility import analyze_development_feasibility_use_case
from use_cases.evidence_review import list_market_evidence_use_case, review_market_evidence_use_case
from use_cases.land_ledger_import import analyze_land_ledger_workbook_use_case
from use_cases.legal_review import review_legal_and_permits_use_case
from use_cases.marketability_review import analyze_marketability_use_case
from use_cases.orchestration_approval import approve_orchestration_run_by_id_use_case
from use_cases.pf_review import run_and_save_pf_review_use_case
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
    find_source_document_by_archive_id_use_case,
    list_source_documents_use_case,
    save_source_document_metadata_use_case,
)

router = APIRouter(prefix="/projects", tags=["projects"])

import json

API_SERVER_ROOT = Path(__file__).resolve().parents[1]
UPLOAD_ARCHIVE_ROOT = API_SERVER_ROOT / "data" / "runtime" / "uploads"

PROJECTS_DB = {}
ORCHESTRATIONS_DB = {}
REPORTS_DB = {}
MARKET_EVIDENCE_DB = {}
project_id_counter = 1

def persist_state() -> None:
    save_state(PROJECTS_DB, REPORTS_DB, ORCHESTRATIONS_DB, project_id_counter, MARKET_EVIDENCE_DB)

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
        persist_state,
        allocate_project_id,
    )

def report_repository() -> ReportRepository:
    return ReportRepository(REPORTS_DB, persist_state)

def source_document_repository() -> SourceDocumentRepository:
    return SourceDocumentRepository(PROJECTS_DB, persist_state)

def market_evidence_repository() -> MarketEvidenceRepository:
    return MarketEvidenceRepository(MARKET_EVIDENCE_DB, persist_state)

def orchestration_repository() -> OrchestrationRepository:
    return OrchestrationRepository(
        ORCHESTRATIONS_DB,
        report_repository(),
        market_evidence_repository(),
        persist_state,
    )

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def safe_upload_suffix(filename: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    if not suffix or len(suffix) > 12:
        return ".bin"
    return suffix

def is_valid_archive_id(value: str | None) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(char in string.hexdigits for char in value)
    )

def project_upload_archive_dir(project_id: str) -> Path:
    archive_root = UPLOAD_ARCHIVE_ROOT.resolve()
    archive_dir = (archive_root / str(project_id)).resolve()
    if archive_root not in archive_dir.parents:
        raise HTTPException(status_code=400, detail="Invalid project archive path")
    return archive_dir

def archive_uploaded_document(
    project_id: str,
    content: bytes,
    original_file_name: str | None,
    content_type: str | None,
) -> Dict[str, Any]:
    archive_id = hashlib.sha256(content).hexdigest()
    suffix = safe_upload_suffix(original_file_name)
    project_archive_dir = project_upload_archive_dir(project_id)
    project_archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = project_archive_dir / f"{archive_id}{suffix}"
    if not archive_path.exists():
        archive_path.write_bytes(content)

    return {
        "archiveId": archive_id,
        "archiveStored": True,
        "archivePath": str(archive_path.relative_to(API_SERVER_ROOT)).replace("\\", "/"),
        "downloadUrl": f"/projects/{project_id}/source-documents/{archive_id}/download",
        "contentType": content_type,
    }

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
    original_file_hash = hashlib.sha256(content).hexdigest()
    archive_meta = archive_uploaded_document(project_id, content, file.filename, file.content_type)
    upload_meta = {
        "originalFileName": file.filename,
        "originalFileHash": original_file_hash,
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

async def parse_uploaded_land_ledger(file: UploadFile) -> Dict[str, Any]:
    suffix = os.path.splitext(file.filename or "")[1] or ".xls"
    uploaded_at = now_iso()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name
    original_file_hash = hashlib.sha256(content).hexdigest()

    try:
        return analyze_land_ledger_workbook_use_case(
            tmp_path,
            {
                "import_id": str(uuid4()),
                "original_file_name": file.filename,
                "original_file_hash": original_file_hash,
                "uploaded_at": uploaded_at,
            },
        ) | {
            "upload": {
                "originalFileName": file.filename,
                "originalFileHash": original_file_hash,
                "uploadedAt": uploaded_at,
            }
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
    project_archive_dir = project_upload_archive_dir(project_id)
    if project_archive_dir.exists():
        shutil.rmtree(project_archive_dir)
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
    ledger = await parse_uploaded_land_ledger(file)
    return {
        "project_id": project_id,
        "landLedger": ledger,
        "note": "PNU and official land price enrichment require external connectors after ledger import.",
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

@router.get("/{project_id}/source-documents/{archive_id}/download")
def download_project_source_document(project_id: str, archive_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    if not is_valid_archive_id(archive_id):
        raise HTTPException(status_code=404, detail="Source document not found")

    document = find_source_document_by_archive_id_use_case(
        source_document_repository(),
        project_id,
        archive_id,
    )
    if not document:
        raise HTTPException(status_code=404, detail="Source document not found")

    archive_path = (
        project_upload_archive_dir(project_id)
        / f"{archive_id}{safe_upload_suffix(document.get('originalFileName'))}"
    )
    if not archive_path.exists():
        raise HTTPException(status_code=404, detail="Archived source document file not found")

    return FileResponse(
        archive_path,
        media_type=document.get("contentType") or "application/octet-stream",
        filename=document.get("originalFileName") or f"{archive_id}{archive_path.suffix}",
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
def create_report_export(project_id: str, report_id: str, input_data: Dict[str, Any] | None = None):
    try:
        export_record = export_saved_report_use_case(report_repository(), project_id, report_id, input_data)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
def download_report_export(project_id: str, report_id: str, export_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    repo = report_repository()
    record = repo.get(project_id, report_id)
    if not record:
        raise HTTPException(status_code=404, detail="Report not found")
    export_record = repo.get_export(record, export_id)
    if not export_record:
        raise HTTPException(status_code=404, detail="Report export not found")
    return Response(
        content=export_record.get("content", ""),
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
