from fastapi import APIRouter, HTTPException, File, UploadFile, Header, Response
from fastapi.responses import FileResponse
from typing import List, Dict, Any
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
    calculate_development_profit, calculate_profit_margin, calculate_cashflow_max_minus,
    calculate_all_in_cost, calculate_exit_sale_rate, calculate_ltv, calculate_ltc, calculate_dscr,
    build_monthly_cash_flow, calculate_pf_metrics, calculate_feasibility_metrics
)
from core.risk_assessment import assess_risk_item, calculate_overall_risk, build_basic_risk_matrix
from agents.development_feasibility_agent import DevelopmentFeasibilityAgent
from agents.legal_review_agent import LegalReviewAgent
from agents.land_ledger_agent import LandLedgerAgent
from agents.marketability_agent import MarketabilityAgent
from agents.orchestration_agent import OrchestrationAgent
from agents.report_agent import ReportAgent
from agents.report_export_agent import ReportExportAgent
from storage import load_state, save_state

router = APIRouter(prefix="/projects", tags=["projects"])

import json
import os

API_SERVER_ROOT = Path(__file__).resolve().parents[1]
UPLOAD_ARCHIVE_ROOT = API_SERVER_ROOT / "data" / "runtime" / "uploads"

PROJECTS_DB = {}
ORCHESTRATIONS_DB = {}
REPORTS_DB = {}
MARKET_EVIDENCE_DB = {}
project_id_counter = 1

def persist_state() -> None:
    save_state(PROJECTS_DB, REPORTS_DB, ORCHESTRATIONS_DB, project_id_counter, MARKET_EVIDENCE_DB)

def normalize_project_record(project_id: str, project_data: Dict[str, Any]) -> Dict[str, Any]:
    now = now_iso()
    record = dict(project_data)
    project = dict(record.get("project", {}))
    if not project:
        project = {
            key: record.pop(key)
            for key in list(record.keys())
            if key in {"name", "type", "status", "review_date"}
        }
    project["id"] = project_id
    project.setdefault("name", record.get("name", f"Project {project_id}"))
    project.setdefault("status", "draft")
    record["project"] = project
    record["id"] = project_id
    record.setdefault("_meta", {})
    record["_meta"].setdefault("created_at", now)
    record["_meta"]["updated_at"] = now
    return record

def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged

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

def project_source_documents(project_id: str) -> List[Dict[str, Any]]:
    project = PROJECTS_DB.get(project_id, {})
    documents = list(project.get("source_documents") or [])
    upload_meta = project.get("_meta", {}).get("upload")
    if isinstance(upload_meta, dict):
        upload_archive_id = upload_meta.get("archiveId") or upload_meta.get("originalFileHash")
        already_listed = any(
            (document.get("archiveId") or document.get("originalFileHash")) == upload_archive_id
            for document in documents
        )
        if not already_listed:
            documents.append(upload_meta)
    return documents

def save_source_document_metadata(project_id: str, upload_meta: Dict[str, Any]) -> None:
    project = PROJECTS_DB[project_id]
    project.setdefault("_meta", {})
    project["_meta"]["upload"] = upload_meta
    documents = project.setdefault("source_documents", [])
    upload_archive_id = upload_meta.get("archiveId") or upload_meta.get("originalFileHash")
    already_listed = any(
        (document.get("archiveId") or document.get("originalFileHash")) == upload_archive_id
        for document in documents
    )
    if not already_listed:
        documents.append(upload_meta)
    persist_state()

def save_report_record(
    project_id: str,
    structured_report: Dict[str, Any],
    report_markdown: str,
    run_id: str | None = None,
) -> Dict[str, Any]:
    version = len(REPORTS_DB.get(project_id, [])) + 1
    record = {
        "report_id": str(uuid4()),
        "project_id": project_id,
        "run_id": run_id,
        "version": version,
        "template": structured_report["template"],
        "title": structured_report.get("title", "AI PF 심사보고서"),
        "status": structured_report.get("status", "draft"),
        "created_at": now_iso(),
        "metadata": {
            "section_count": structured_report["qualityChecks"]["actualSectionCount"],
            "expected_section_count": structured_report["qualityChecks"]["expectedSectionCount"],
            "complete": structured_report["qualityChecks"]["isComplete"],
        },
        "report": structured_report,
        "report_markdown": report_markdown,
        "exports": [],
    }
    REPORTS_DB.setdefault(project_id, []).append(record)
    persist_state()
    return record

def save_orchestration_run(project_id: str, run: Dict[str, Any]) -> Dict[str, Any]:
    ORCHESTRATIONS_DB.setdefault(project_id, {})[run["run_id"]] = run
    save_market_evidence_records(project_id, run)
    persist_state()

    if "phase11Report" in run["artifacts"]:
        report_artifact = run["artifacts"]["phase11Report"]
        report_record = save_report_record(
            project_id,
            report_artifact["report"],
            report_artifact["report_markdown"],
            run["run_id"],
        )
        run["artifacts"]["reportRecord"] = report_metadata(report_record)
        persist_state()

    return run

def save_market_evidence_records(project_id: str, run: Dict[str, Any]) -> None:
    evidence_records = (
        run.get("artifacts", {})
        .get("marketEvidenceReview", {})
        .get("evidenceRecords", [])
    )
    if not evidence_records:
        return

    created_at = now_iso()
    stored = MARKET_EVIDENCE_DB.setdefault(project_id, {})
    for index, record in enumerate(evidence_records):
        evidence_id = record.get("evidenceId") or f"market-evidence-{index + 1:03d}"
        record_key = f"{run['run_id']}:{evidence_id}"
        validation_status = record.get("validationStatus", "not_checked")
        stored[record_key] = {
            **record,
            "recordKey": record_key,
            "projectId": project_id,
            "runId": run["run_id"],
            "workflow": run.get("workflow"),
            "createdAt": created_at,
            "reviewStatus": "pending_review" if validation_status != "pass" else "auto_validated",
        }

def apply_parsed_project_data(project_id: str, parsed_data: Dict[str, Any]) -> Dict[str, Any]:
    if "project" not in parsed_data:
        parsed_data["project"] = {}
    parsed_data["project"]["id"] = project_id
    PROJECTS_DB[project_id].update(parsed_data)
    persist_state()
    return PROJECTS_DB[project_id]

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

        document_market_evidence = parsed_data.get("document_market_evidence")
        if isinstance(document_market_evidence, dict):
            document_market_evidence.setdefault("sourceDocument", upload_meta)
            document_market_evidence.setdefault("parserVersion", PARSER_AGENT_VERSION)

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
        return LandLedgerAgent().analyze_xls(
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

def report_metadata(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "report_id": record["report_id"],
        "project_id": record["project_id"],
        "run_id": record.get("run_id"),
        "version": record.get("version", 1),
        "template": record["template"],
        "title": record["title"],
        "status": record["status"],
        "created_at": record["created_at"],
        "metadata": record["metadata"],
        "export_count": len(record.get("exports", [])),
        "latest_exported_at": record.get("exports", [{}])[-1].get("created_at") if record.get("exports") else None,
    }

def export_metadata(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if key != "content"
    }

def find_report_record(project_id: str, report_id: str) -> Dict[str, Any]:
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    for record in REPORTS_DB.get(project_id, []):
        if record.get("report_id") == report_id:
            record.setdefault("exports", [])
            record.setdefault("version", 1)
            return record
    raise HTTPException(status_code=404, detail="Report not found")

def find_report_export(report_record: Dict[str, Any], export_id: str) -> Dict[str, Any]:
    for record in report_record.get("exports", []):
        if record.get("export_id") == export_id:
            return record
    raise HTTPException(status_code=404, detail="Report export not found")

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
    return list(PROJECTS_DB.values())

@router.post("/")
def create_project(project_data: Dict[str, Any]):
    global project_id_counter
    pid = str(project_id_counter)
    PROJECTS_DB[pid] = normalize_project_record(pid, project_data)
    project_id_counter += 1
    persist_state()
    return PROJECTS_DB[pid]

@router.get("/{project_id}")
def get_project(project_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    return PROJECTS_DB[project_id]

@router.put("/{project_id}")
def update_project(project_id: str, project_data: Dict[str, Any]):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    PROJECTS_DB[project_id] = normalize_project_record(
        project_id,
        deep_merge(PROJECTS_DB[project_id], project_data),
    )
    persist_state()
    return PROJECTS_DB[project_id]

@router.delete("/{project_id}")
def delete_project(project_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    deleted = PROJECTS_DB.pop(project_id)
    REPORTS_DB.pop(project_id, None)
    ORCHESTRATIONS_DB.pop(project_id, None)
    MARKET_EVIDENCE_DB.pop(project_id, None)
    project_archive_dir = project_upload_archive_dir(project_id)
    if project_archive_dir.exists():
        shutil.rmtree(project_archive_dir)
    persist_state()
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
            save_source_document_metadata(project_id, upload_meta)
        return {
            "project_id": project_id,
            "status": "host_llm_required",
            "upload": parsed_data,
        }

    return apply_parsed_project_data(project_id, parsed_data)

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
            save_source_document_metadata(project_id, upload_meta)
        return {
            "project_id": project_id,
            "status": "host_llm_required",
            "upload": parsed_data,
            "message": "Host LLM extraction is required before PF review orchestration can run.",
        }

    project_data = apply_parsed_project_data(project_id, parsed_data)
    run = OrchestrationAgent().run_pf_review(
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
        "run": save_orchestration_run(project_id, run),
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
    source_data = deep_merge(PROJECTS_DB[project_id], input_data or {})
    return DevelopmentFeasibilityAgent().analyze(source_data)

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
    source_data = deep_merge(PROJECTS_DB[project_id], input_data or {})
    return MarketabilityAgent().analyze(source_data)

@router.get("/{project_id}/source-documents")
def list_project_source_documents(project_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    return project_source_documents(project_id)

@router.get("/{project_id}/source-documents/{archive_id}/download")
def download_project_source_document(project_id: str, archive_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    if not is_valid_archive_id(archive_id):
        raise HTTPException(status_code=404, detail="Source document not found")

    document = next(
        (
            item
            for item in project_source_documents(project_id)
            if (item.get("archiveId") or item.get("originalFileHash")) == archive_id
        ),
        None,
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
    records = list(MARKET_EVIDENCE_DB.get(project_id, {}).values())
    if run_id:
        records = [record for record in records if record.get("runId") == run_id]
    return sorted(
        records,
        key=lambda item: (
            item.get("createdAt") or "",
            item.get("evidenceType") or "",
            item.get("evidenceId") or "",
        ),
    )

@router.post("/{project_id}/market-evidence/review")
def review_project_market_evidence(project_id: str, input_data: Dict[str, Any]):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")

    record_key = input_data.get("record_key") or input_data.get("recordKey")
    if not record_key:
        raise HTTPException(status_code=400, detail="record_key is required")

    record = MARKET_EVIDENCE_DB.get(project_id, {}).get(record_key)
    if not record:
        raise HTTPException(status_code=404, detail="Market evidence record not found")

    review_status = input_data.get("review_status") or input_data.get("reviewStatus")
    allowed_statuses = {"pending_review", "approved", "rejected", "needs_revision", "auto_validated"}
    if review_status not in allowed_statuses:
        raise HTTPException(status_code=400, detail=f"review_status must be one of {sorted(allowed_statuses)}")

    reviewed_at = now_iso()
    review_entry = {
        "reviewStatus": review_status,
        "reviewedAt": reviewed_at,
        "reviewedBy": input_data.get("reviewed_by") or input_data.get("reviewedBy") or "system",
        "reviewComment": input_data.get("comment", ""),
    }
    record["reviewStatus"] = review_status
    record["reviewedAt"] = reviewed_at
    record["reviewedBy"] = review_entry["reviewedBy"]
    record["reviewComment"] = review_entry["reviewComment"]
    record.setdefault("reviewHistory", []).append(review_entry)
    persist_state()
    return record

@router.post("/{project_id}/legal/review")
def review_legal_and_permits(project_id: str, input_data: Dict[str, Any] | None = None):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    input_data = input_data or {}
    source_data = deep_merge(PROJECTS_DB[project_id], input_data.get("project_data", {}))
    return LegalReviewAgent().review(source_data, input_data.get("law_context"))

@router.post("/{project_id}/reports/generate")
def generate_report(project_id: str, input_data: Dict[str, Any]):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")

    source_data = deep_merge(PROJECTS_DB[project_id], input_data)
    agent = ReportAgent()
    structured_report = agent.generate_structured_report(source_data)
    md_report = agent.generate_report(source_data)
    report_record = save_report_record(project_id, structured_report, md_report)
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
    return [report_metadata(record) for record in REPORTS_DB.get(project_id, [])]

@router.get("/{project_id}/reports/{report_id}")
def get_report(project_id: str, report_id: str):
    record = find_report_record(project_id, report_id)
    return {
        **report_metadata(record),
        "report": record.get("report"),
        "report_markdown": record.get("report_markdown"),
        "exports": [export_metadata(export) for export in record.get("exports", [])],
    }

@router.post("/{project_id}/reports/{report_id}/exports")
def create_report_export(project_id: str, report_id: str, input_data: Dict[str, Any] | None = None):
    record = find_report_record(project_id, report_id)
    input_data = input_data or {}
    export_format = input_data.get("format", "markdown")
    try:
        rendered = ReportExportAgent().export(record, export_format)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    export_record = {
        "export_id": str(uuid4()),
        "report_id": report_id,
        "project_id": project_id,
        "version": record.get("version", 1),
        "created_at": now_iso(),
        **rendered,
    }
    record.setdefault("exports", []).append(export_record)
    persist_state()
    return export_metadata(export_record)

@router.get("/{project_id}/reports/{report_id}/exports")
def list_report_exports(project_id: str, report_id: str):
    record = find_report_record(project_id, report_id)
    return [export_metadata(export) for export in record.get("exports", [])]

@router.get("/{project_id}/reports/{report_id}/exports/{export_id}/download")
def download_report_export(project_id: str, report_id: str, export_id: str):
    record = find_report_record(project_id, report_id)
    export_record = find_report_export(record, export_id)
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
    run = OrchestrationAgent().run_pf_review(
        project_id,
        source_data,
        input_data.get("options", {}),
    )
    return save_orchestration_run(project_id, run)

@router.get("/{project_id}/orchestrations")
def list_project_orchestrations(project_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")

    runs = ORCHESTRATIONS_DB.get(project_id, {}).values()
    return [
        {
            "run_id": run["run_id"],
            "project_id": run["project_id"],
            "workflow": run["workflow"],
            "status": run["status"],
            "started_at": run["started_at"],
            "completed_at": run["completed_at"],
            "step_count": len(run["steps"]),
            "errors": run["errors"],
            "approval": run.get("approval"),
        }
        for run in runs
    ]

@router.get("/{project_id}/orchestrations/{run_id}")
def get_project_orchestration(project_id: str, run_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")

    run = ORCHESTRATIONS_DB.get(project_id, {}).get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Orchestration run not found")
    return run

@router.post("/{project_id}/orchestrations/{run_id}/approve")
def approve_project_orchestration(project_id: str, run_id: str, input_data: Dict[str, Any] | None = None):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")

    run = ORCHESTRATIONS_DB.get(project_id, {}).get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Orchestration run not found")
    if run["status"] not in {"completed", "completed_with_warnings"}:
        raise HTTPException(status_code=409, detail="Only completed runs can be approved")

    input_data = input_data or {}
    run["status"] = "approved"
    run["approval"] = {
        "approved_at": now_iso(),
        "approved_by": input_data.get("approved_by", "system"),
        "comment": input_data.get("comment", ""),
    }
    persist_state()
    return run
