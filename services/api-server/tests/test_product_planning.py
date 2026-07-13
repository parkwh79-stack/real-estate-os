from fastapi.testclient import TestClient

from agents.product_planning_agent import ProductPlanningAgent
from main import app
from routers import projects
from use_cases.product_planning import compare_product_planning_use_case


def _isolate(tmp_path, monkeypatch, name):
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / name))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(projects, "persist_state", lambda: None)


def _project_body():
    return {
        "project": {"name": "상품기획 QA", "type": "mixed_use"},
        "land": {"address": "서울", "area_sqm": 5000},
        "regulation": {"applied_far": 300, "applied_bcr": 60},
    }


def test_plan_asset_derives_units_parking_deterministically():
    agent = ProductPlanningAgent()
    apt = agent.plan_asset(asset_type="apartment", land_area_sqm=5000, applied_far=300, applied_bcr=60)
    # GFA = 5000 * 300/100 = 15,000㎡; 전용 = *0.75 = 11,250㎡
    assert apt["grossFloorAreaSqm"] == 15000
    assert apt["exclusiveAreaSqm"] == 11250
    # 세대수 = 전용평(3403.x) / 25.7 → integer, parking = units * 1.2
    assert apt["unitCount"] and apt["unitCount"] > 0
    assert apt["parkingSpaces"] == round(apt["unitCount"] * 1.2)
    assert "efficiency" in apt["benchmarksUsed"]
    # No price given → sales revenue not fabricated
    assert apt["salesRevenue"] is None
    assert any("분양가" in n for n in apt["notes"])


def test_plan_asset_price_and_efficiency_override():
    agent = ProductPlanningAgent()
    row = agent.plan_asset(
        asset_type="apartment", land_area_sqm=5000, applied_far=300,
        overrides={"efficiency": 0.8, "price_per_py": 30_000_000},
    )
    # efficiency override applied (not in benchmarksUsed)
    assert row["efficiency"] == 0.8
    assert "efficiency" not in row["benchmarksUsed"]
    # revenue computed from price basis
    assert row["salesRevenue"] and row["salesRevenue"] > 0


def test_income_asset_noi_requires_rent():
    agent = ProductPlanningAgent()
    no_rent = agent.plan_asset(asset_type="logistics", land_area_sqm=10000, applied_far=200)
    assert no_rent["incomeAsset"] is True
    assert no_rent["annualNoi"] is None
    assert any("임대료" in n for n in no_rent["notes"])
    with_rent = agent.plan_asset(
        asset_type="logistics", land_area_sqm=10000, applied_far=200,
        overrides={"rent_per_py_month": 35_000, "cap_rate": 0.055},
    )
    assert with_rent["annualNoi"] and with_rent["annualNoi"] > 0
    assert with_rent["incomeValue"] and with_rent["incomeValue"] > 0


def test_compare_use_case_defaults_and_top_by_value():
    result = compare_product_planning_use_case({
        **_project_body(),
        "product_mix": [{"productType": "apartment"}, {"productType": "commercial"}],
    }, scenarios=[
        {"assetType": "apartment", "overrides": {"price_per_py": 30_000_000}},
        {"assetType": "officetel", "overrides": {"price_per_py": 22_000_000}},
    ])
    assert result["schemaVersion"] == "realytics-product-planning-v1"
    assert result["scenarioCount"] == 2
    assert result["pricedCount"] == 2
    # apartment (bigger 전용률·평형, higher price) should top by sales revenue
    assert result["topByValue"]["assetType"] == "apartment"
    assert result["availableAssetTypes"]


def test_product_planning_endpoint(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch, "product-planning.sqlite3")
    client = TestClient(app)
    project_id = client.post("/projects/", json=_project_body()).json()["project"]["id"]
    res = client.post(f"/projects/{project_id}/product-planning/compare", json={})
    assert res.status_code == 200
    body = res.json()
    assert body["schemaVersion"] == "realytics-product-planning-v1"
    assert body["landAreaSqm"] == 5000
    assert body["rows"]
    client.delete(f"/projects/{project_id}")
