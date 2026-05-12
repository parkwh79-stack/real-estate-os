from fastapi import APIRouter, HTTPException, File, UploadFile
from typing import List, Dict, Any
from datetime import datetime, timezone
import tempfile
import os
from uuid import uuid4
from agents.parser_agent import ParserAgent

from core.calculations import (
    calculate_development_profit, calculate_profit_margin, calculate_cashflow_max_minus,
    calculate_all_in_cost, calculate_exit_sale_rate, calculate_ltv, calculate_ltc, calculate_dscr
)
from core.risk_assessment import assess_risk_item, calculate_overall_risk
from agents.development_feasibility_agent import DevelopmentFeasibilityAgent
from agents.legal_review_agent import LegalReviewAgent
from agents.marketability_agent import MarketabilityAgent
from agents.orchestration_agent import OrchestrationAgent
from agents.report_agent import ReportAgent
from storage import load_state, save_state

router = APIRouter(prefix="/projects", tags=["projects"])

import json
import os

PROJECTS_DB = {}
ORCHESTRATIONS_DB = {}
REPORTS_DB = {}
project_id_counter = 1

def persist_state() -> None:
    save_state(PROJECTS_DB, REPORTS_DB, ORCHESTRATIONS_DB, project_id_counter)

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

def save_report_record(
    project_id: str,
    structured_report: Dict[str, Any],
    report_markdown: str,
    run_id: str | None = None,
) -> Dict[str, Any]:
    record = {
        "report_id": str(uuid4()),
        "project_id": project_id,
        "run_id": run_id,
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
    }
    REPORTS_DB.setdefault(project_id, []).append(record)
    persist_state()
    return record

def report_metadata(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "report_id": record["report_id"],
        "project_id": record["project_id"],
        "run_id": record.get("run_id"),
        "template": record["template"],
        "title": record["title"],
        "status": record["status"],
        "created_at": record["created_at"],
        "metadata": record["metadata"],
    }

def load_fixtures():
    global project_id_counter
    state = load_state()
    if state:
        PROJECTS_DB.update(state["projects"])
        REPORTS_DB.update(state["reports"])
        ORCHESTRATIONS_DB.update(state["orchestrations"])
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
    project_data["id"] = pid
    PROJECTS_DB[pid] = project_data
    project_id_counter += 1
    persist_state()
    return project_data

@router.get("/{project_id}")
def get_project(project_id: str):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    return PROJECTS_DB[project_id]

@router.put("/{project_id}")
def update_project(project_id: str, project_data: Dict[str, Any]):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    PROJECTS_DB[project_id].update(project_data)
    persist_state()
    return PROJECTS_DB[project_id]

@router.post("/{project_id}/upload")
async def upload_business_plan(project_id: str, file: UploadFile = File(...)):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
        
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name
        
    try:
        agent = ParserAgent()
        text = agent.parse_document(tmp_path)
        parsed_data = agent.extract_project_data(text)
        
        # Ensure project ID is retained
        if "project" not in parsed_data:
            parsed_data["project"] = {}
        parsed_data["project"]["id"] = project_id
        
        # Deep merge could be better, but update works for MVP top-level keys
        PROJECTS_DB[project_id].update(parsed_data)
        persist_state()
        
        return PROJECTS_DB[project_id]
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

@router.post("/{project_id}/feasibility/calculate")
def calculate_feasibility(project_id: str, input_data: Dict[str, Any]):
    # Extracted from standard fixture structure `feasibility_input`
    rev = input_data.get("total_revenue", 0)
    cost = input_data.get("total_cost", 0)
    
    profit = calculate_development_profit(rev, cost)
    margin = calculate_profit_margin(profit, rev)
    
    result = {
        "development_profit": profit,
        "profit_margin": margin
    }
    return result

@router.post("/{project_id}/development-feasibility/analyze")
def analyze_development_feasibility(project_id: str, input_data: Dict[str, Any] | None = None):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    source_data = deep_merge(PROJECTS_DB[project_id], input_data or {})
    return DevelopmentFeasibilityAgent().analyze(source_data)

@router.post("/{project_id}/cashflow/calculate")
def calculate_cashflow(project_id: str, input_data: Dict[str, Any]):
    # Simplified mock implementation
    cfs = input_data.get("cashflows", [])
    max_minus = calculate_cashflow_max_minus(cfs)
    return {"max_minus": max_minus, "cashflows": cfs}

@router.post("/{project_id}/pf/structure")
def calculate_pf_structure(project_id: str, input_data: Dict[str, Any]):
    tranches = input_data.get("tranches", [])
    total_pf = sum(t.get("amount", 0) for t in tranches)
    
    # Mock totals
    total_rev = input_data.get("total_revenue", 0)
    total_cost = input_data.get("total_cost", 0)
    
    ltv = calculate_ltv(total_pf, total_rev)
    ltc = calculate_ltc(total_pf, total_cost)
    exit_rate = calculate_exit_sale_rate(total_pf, 0, total_rev) # Simplified
    
    return {
        "total_pf_amount": total_pf,
        "ltv": ltv,
        "ltc": ltc,
        "exit_sale_rate": exit_rate
    }

@router.post("/{project_id}/risk/assess")
def assess_risk(project_id: str, input_data: Dict[str, Any]):
    raw_items = input_data.get("risk_items", [])
    assessed = [assess_risk_item(item) for item in raw_items]
    overall = calculate_overall_risk(assessed)
    overall["items"] = assessed
    return overall

@router.post("/{project_id}/marketability/analyze")
def analyze_marketability(project_id: str, input_data: Dict[str, Any] | None = None):
    if project_id not in PROJECTS_DB:
        raise HTTPException(status_code=404, detail="Project not found")
    source_data = deep_merge(PROJECTS_DB[project_id], input_data or {})
    return MarketabilityAgent().analyze(source_data)

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
    ORCHESTRATIONS_DB.setdefault(project_id, {})[run["run_id"]] = run
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
