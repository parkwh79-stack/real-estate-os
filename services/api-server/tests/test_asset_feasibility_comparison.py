from fastapi.testclient import TestClient

from main import app
from routers import projects


def _create(client, body):
    return client.post("/projects/", json=body).json()["project"]["id"]


def _isolate(tmp_path, monkeypatch, name):
    """Run against a throwaway SQLite file so tests never touch the shared runtime DB."""
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / name))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(projects, "persist_state", lambda: None)


def test_asset_comparison_ranks_scenarios_by_profit_margin(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch, "asset-ranking.sqlite3")
    client = TestClient(app)
    project_id = _create(
        client,
        {
            "project": {"name": "Asset Comparison Ranking", "type": "mixed_use"},
            "land": {"address": "대구 본리동", "area_sqm": 4691.9},
            "regulation": {"applied_far": 250, "applied_bcr": 60},
            "feasibility_input": {"total_revenue": 100000000000, "total_cost": 85000000000},
        },
    )

    res = client.post(
        f"/projects/{project_id}/asset-comparison/analyze",
        json={
            "scenarios": [
                {
                    "scenarioId": "apt",
                    "label": "아파트안",
                    "assetType": "apartment",
                    "overrides": {"feasibility_input": {"total_revenue": 120000000000, "total_cost": 90000000000}},
                },
                {
                    "scenarioId": "com",
                    "label": "상업안",
                    "assetType": "commercial",
                    "overrides": {"feasibility_input": {"total_revenue": 100000000000, "total_cost": 85000000000}},
                },
            ]
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["schemaVersion"] == "realytics-asset-feasibility-comparison-v1"
    assert body["comparableCount"] == 2
    assert [row["scenarioId"] for row in body["ranking"]] == ["apt", "com"]
    assert body["ranking"][0]["rank"] == 1
    # apartment margin 0.25 > commercial margin 0.15 (deterministic, not fabricated)
    assert abs(body["ranking"][0]["profitMargin"] - 0.25) < 1e-9
    assert body["recommendation"]["assetType"] == "apartment"
    assert body["recommendation"]["basis"] == "ranked_by_profit_margin"
    # asset profile metadata differs per asset type
    apt_row = next(r for r in body["rows"] if r["scenarioId"] == "apt")
    com_row = next(r for r in body["rows"] if r["scenarioId"] == "com")
    assert apt_row["ltvRiskThreshold"] != com_row["ltvRiskThreshold"]

    client.delete(f"/projects/{project_id}")


def test_asset_comparison_flags_insufficient_data_without_fabricating(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch, "asset-nodata.sqlite3")
    client = TestClient(app)
    # No feasibility_input -> no revenue/cost basis for any scenario.
    project_id = _create(
        client,
        {
            "project": {"name": "Asset Comparison No Data", "type": "apartment"},
            "land": {"address": "미상", "area_sqm": 3000},
            "regulation": {"applied_far": 200, "applied_bcr": 50},
        },
    )

    res = client.post(
        f"/projects/{project_id}/asset-comparison/analyze",
        json={
            "scenarios": [
                {"scenarioId": "apt", "assetType": "apartment"},
                {"scenarioId": "log", "assetType": "logistics"},
            ]
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["comparableCount"] == 0
    assert body["insufficientDataCount"] == 2
    assert body["ranking"] == []
    assert body["recommendation"] is None
    # No invented profit numbers on unrankable scenarios.
    for row in body["rows"]:
        assert row["status"] == "insufficient_data"
        assert row["developmentProfit"] == 0
    note_ids = {note["scenarioId"] for note in body["dataQualityNotes"]}
    assert note_ids == {"apt", "log"}
    assert any("총수입" in reason for note in body["dataQualityNotes"] for reason in note["reasons"])

    client.delete(f"/projects/{project_id}")


def test_asset_comparison_defaults_to_project_asset_types(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch, "asset-default.sqlite3")
    client = TestClient(app)
    project_id = _create(
        client,
        {
            "project": {"name": "Asset Comparison Default", "type": "mixed_use"},
            "land": {"address": "서울", "area_sqm": 5000},
            "regulation": {"applied_far": 300, "applied_bcr": 60},
            "feasibility_input": {"total_revenue": 200000000000, "total_cost": 150000000000},
            "product_mix": [
                {"productType": "apartment"},
                {"productType": "commercial"},
            ],
        },
    )

    res = client.post(f"/projects/{project_id}/asset-comparison/analyze", json={})

    assert res.status_code == 200
    body = res.json()
    assert body["baseProgram"]["landAreaSqm"] == 5000
    asset_types = {row["assetType"] for row in body["rows"]}
    assert {"apartment", "commercial"}.issubset(asset_types)
    assert body["comparableCount"] >= 1

    client.delete(f"/projects/{project_id}")
