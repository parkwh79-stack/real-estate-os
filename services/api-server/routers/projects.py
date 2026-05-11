from fastapi import APIRouter, HTTPException, File, UploadFile
from typing import List, Dict, Any
import tempfile
import os
from agents.parser_agent import ParserAgent

from core.calculations import (
    calculate_development_profit, calculate_profit_margin, calculate_cashflow_max_minus,
    calculate_all_in_cost, calculate_exit_sale_rate, calculate_ltv, calculate_ltc, calculate_dscr
)
from core.risk_assessment import assess_risk_item, calculate_overall_risk
from agents.report_agent import ReportAgent

router = APIRouter(prefix="/projects", tags=["projects"])

import json
import os

PROJECTS_DB = {}
project_id_counter = 1

def load_fixtures():
    global project_id_counter
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

@router.post("/{project_id}/reports/generate")
def generate_report(project_id: str, input_data: Dict[str, Any]):
    agent = ReportAgent()
    md_report = agent.generate_report(input_data)
    return {"report_markdown": md_report}

@router.get("/{project_id}/reports")
def get_reports(project_id: str):
    # Mock
    return []
