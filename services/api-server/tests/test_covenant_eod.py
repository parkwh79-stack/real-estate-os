from fastapi.testclient import TestClient

from agents.covenant_eod_agent import CovenantEodAgent
from main import app
from routers import projects
from use_cases.covenant_eod import evaluate_covenant_eod_use_case
from use_cases.credit_documents import _merge_covenant_overrides, generate_credit_documents


def _isolate(tmp_path, monkeypatch, name):
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / name))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(projects, "persist_state", lambda: None)


def _project_body():
    return {
        "project": {"name": "Covenant QA", "type": "apartment"},
        "land": {"address": "서울", "area_sqm": 5000},
        "regulation": {"applied_far": 250, "applied_bcr": 60},
        "feasibility_input": {
            "total_revenue": 100000000000,
            "total_cost": 85000000000,
            "land_cost": 30000000000,
            "construction_cost": 50000000000,
            "finance_cost": 5000000000,
        },
        "pf_structure": [
            {"name": "Tr.A", "priority": "senior", "amount": 50000000000},
            {"name": "Tr.B", "priority": "mezzanine", "amount": 10000000000},
        ],
    }


def test_covenant_eod_derives_thresholds_with_source_locators():
    result = evaluate_covenant_eod_use_case(_project_body())
    assert result["schemaVersion"] == "realytics-covenant-eod-v1"
    codes = {c["code"] for c in result["covenants"]}
    # Deal-derived + policy covenants present
    assert {"TARGET_SALES_RATE", "LTV_CEILING", "LTC_CEILING", "RESPONSIBLE_COMPLETION",
            "CASH_SWEEP_TRIGGER", "EQUITY_FIRST_IN", "FUND_EXECUTION_ORDER", "COST_OVERRUN_BUFFER"}.issubset(codes)
    # Every covenant carries a source locator (provenance invariant)
    for cov in result["covenants"]:
        assert cov.get("sourceLocator"), f"{cov['code']} missing sourceLocator"
    # Target sales rate is deterministic (from exit sale rate), stated as a ratio 0..1
    target = next(c for c in result["covenants"] if c["code"] == "TARGET_SALES_RATE")
    assert 0 < target["thresholdValue"] <= 1
    assert target["thresholdType"] == "min"
    # Execution order follows tranche priority then 시공사
    fund_order = next(c for c in result["covenants"] if c["code"] == "FUND_EXECUTION_ORDER")
    assert fund_order["thresholdValue"][0] == "Tr.A"
    assert fund_order["thresholdValue"][-1] == "시공사"
    # EOD conditions present
    eod_codes = {e["code"] for e in result["eodConditions"]}
    assert {"EOD_TARGET_SALES_MISS", "EOD_LTV_BREACH", "EOD_COMPLETION_FAILURE"}.issubset(eod_codes)


def test_covenant_eod_no_fabrication_when_inputs_missing():
    # No feasibility_input → no revenue/cost basis → sales/equity/cost covenants cannot be derived.
    thin = {"project": {"name": "Thin", "type": "apartment"}, "land": {"area_sqm": 3000}, "regulation": {"applied_far": 200}}
    result = evaluate_covenant_eod_use_case(thin)
    codes = {c["code"] for c in result["covenants"]}
    # Target sales rate cannot be fabricated without revenue/cost
    assert "TARGET_SALES_RATE" not in codes
    note_codes = {n["code"] for n in result["dataQualityNotes"]}
    assert "TARGET_SALES_RATE" in note_codes
    # Responsible completion is emitted but flagged needs_evidence (no construction cost)
    rc = next(c for c in result["covenants"] if c["code"] == "RESPONSIBLE_COMPLETION")
    assert rc["status"] == "needs_evidence"
    # LTC ceiling is a labelled policy default, not deal evidence
    ltc = next(c for c in result["covenants"] if c["code"] == "LTC_CEILING")
    assert ltc["sourceLocator"]["sourceType"] == "lender_policy_default"


def test_covenant_eod_income_asset_dscr_requires_noi():
    agent = CovenantEodAgent()
    analysis = {
        "developerReturns": {"breakEvenSaleRate": 0.7},
        "pfReadiness": {"exitSaleRate": 0.75, "ltvByRevenue": 0.6, "requiredEquity": 20000000000},
        "costs": {"constructionCost": 40000000000, "totalCost": 80000000000},
        "revenue": {"totalRevenue": 100000000000},
        "assetCalculationProfiles": {"profiles": {"logistics": {"ltvRiskThreshold": 0.70}}},
    }
    # Without NOI → DSCR covenant is a needs-evidence note, not fabricated
    no_noi = agent.build(analysis, asset_type="logistics", pf_structure=[])
    assert "MIN_DSCR" not in {c["code"] for c in no_noi["covenants"]}
    assert "MIN_DSCR" in {n["code"] for n in no_noi["dataQualityNotes"]}
    # With NOI → active DSCR covenant + EOD
    with_noi = agent.build({**analysis, "logisticsAnalysis": {"noi": 6000000000}}, asset_type="logistics", pf_structure=[])
    dscr = next(c for c in with_noi["covenants"] if c["code"] == "MIN_DSCR")
    assert dscr["status"] == "active"
    assert "EOD_DSCR_MISS" in {e["code"] for e in with_noi["eodConditions"]}


def test_covenant_eod_termsheet_consumable_shape():
    result = evaluate_covenant_eod_use_case(_project_body())
    ts = result["termSheetCovenants"]
    assert isinstance(ts.get("eod"), list) and ts["eod"]
    assert ts.get("execution_order") and ts["execution_order"][-1] == "시공사"
    assert isinstance(ts.get("revenue_distribution"), str) and "상환계좌" in ts["revenue_distribution"]


def test_covenant_eod_reviewer_mapping():
    result = evaluate_covenant_eod_use_case(_project_body())
    by_code = {c["code"]: c for c in result["covenants"]}
    assert by_code["TARGET_SALES_RATE"]["responsibleReviewer"]["role"] == "market_stress_tester"
    assert by_code["LTV_CEILING"]["responsibleReviewer"]["role"] == "credit_reviewer"
    assert by_code["RESPONSIBLE_COMPLETION"]["responsibleReviewer"]["role"] == "construction_risk"
    assert by_code["COST_OVERRUN_BUFFER"]["responsibleReviewer"]["role"] == "construction_risk"
    # Every covenant + EOD carries a reviewer with a Korean label
    for cov in result["covenants"]:
        assert cov["responsibleReviewer"]["label"]
    eod_by_code = {e["code"]: e for e in result["eodConditions"]}
    assert eod_by_code["EOD_COMPLETION_FAILURE"]["responsibleReviewer"]["role"] == "construction_risk"
    assert "market_stress_tester" in result["summary"]["reviewerRoles"]


def test_covenant_sheet_markdown_built():
    result = evaluate_covenant_eod_use_case(_project_body())
    sheet = result["covenantSheet"]
    assert sheet["schemaVersion"] == "realytics-covenant-sheet-v1"
    md = sheet["markdown"]
    assert md.startswith("# Covenant Sheet")
    assert "재무·사업 커버넌트" in md
    assert "기한이익상실(EOD) 조건" in md
    assert "여신 심사역" in md  # reviewer label rendered
    assert "시공사" in md  # execution order in the covenant table


def test_credit_documents_consume_covenant_eod():
    docs = generate_credit_documents(_project_body(), {"pfStructure": {"ltv": 0.5}})
    # Derived covenant/EOD surfaced for audit
    assert docs["covenantEod"]["schemaVersion"] == "realytics-covenant-eod-v1"
    assert docs["covenantEod"]["summary"]["covenantCount"] >= 6
    # Term sheet composed, and the derived execution order (tranche → 시공사) reaches it
    assert docs["termSheet"]["schemaVersion"] == "realytics-term-sheet-v1"
    assert "시공사" in docs["termSheet"]["markdown"]


def test_merge_covenant_overrides_reviewer_wins():
    merged = _merge_covenant_overrides(
        {"covenants": {"eod": ["리뷰어 지정 EOD"]}},
        {"eod": ["파생 EOD"], "execution_order": ["Tr.A", "시공사"]},
    )
    # Explicit reviewer override wins; derived fills the gap
    assert merged["covenants"]["eod"] == ["리뷰어 지정 EOD"]
    assert merged["covenants"]["execution_order"] == ["Tr.A", "시공사"]


def test_covenant_eod_endpoint(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch, "covenant-eod.sqlite3")
    client = TestClient(app)
    project_id = client.post("/projects/", json=_project_body()).json()["project"]["id"]
    res = client.post(f"/projects/{project_id}/covenant-eod/evaluate", json={})
    assert res.status_code == 200
    body = res.json()
    assert body["schemaVersion"] == "realytics-covenant-eod-v1"
    assert body["summary"]["covenantCount"] >= 6
    assert body["summary"]["eodConditionCount"] >= 3
    client.delete(f"/projects/{project_id}")
