from __future__ import annotations

from fastapi.testclient import TestClient

from main import app
from use_cases.covenant_monitoring import normalize_thresholds
from use_cases.portfolio_monitoring import build_portfolio_monitoring


def _monitoring(status, *, breach=0, watch=0, alerts=None, checks=None, generated="2026-06-24T00:00:00Z"):
    return {
        "overallStatus": status,
        "generatedAt": generated,
        "summary": {"breachCount": breach, "watchCount": watch, "checkedCount": len(checks or [])},
        "checks": checks or [],
        "alerts": alerts or [],
    }


def test_portfolio_orders_worst_first_and_counts_status():
    entries = [
        {"projectId": "ok1", "name": "정상딜", "monitoring": _monitoring("ok", checks=[{"key": "ltv", "value": 0.5}])},
        {
            "projectId": "bad1",
            "name": "위반딜",
            "monitoring": _monitoring(
                "breach",
                breach=1,
                checks=[{"key": "ltv", "value": 0.82}],
                alerts=[{"code": "COVENANT_LTV_BREACH", "severity": "critical", "covenant": "LTV", "message": "x", "recommendedAction": "y"}],
            ),
        },
        {
            "projectId": "watch1",
            "name": "근접딜",
            "monitoring": _monitoring(
                "watch",
                watch=1,
                checks=[{"key": "exit_sale_rate", "value": 0.74}],
                alerts=[{"code": "COVENANT_EXIT_SALE_RATE_WATCH", "severity": "caution", "covenant": "Exit 분양률", "message": "x", "recommendedAction": "y"}],
            ),
        },
    ]

    result = build_portfolio_monitoring(entries)

    assert result["summary"]["projectCount"] == 3
    assert result["summary"]["monitoredCount"] == 3
    assert result["summary"]["statusCounts"] == {"breach": 1, "watch": 1, "ok": 1, "no_data": 0}
    assert result["summary"]["worstStatus"] == "breach"
    assert result["summary"]["totalCriticalAlerts"] == 1
    assert result["summary"]["totalCautionAlerts"] == 1
    # worst-first ordering
    assert [p["projectId"] for p in result["projects"]] == ["bad1", "watch1", "ok1"]
    # critical-first alert feed
    assert result["alertFeed"][0]["severity"] == "critical"
    assert result["alertFeed"][0]["projectId"] == "bad1"


def test_portfolio_marks_no_data_when_no_metrics():
    entries = [{"projectId": "empty", "name": "자료없음", "monitoring": _monitoring("no_data", checks=[])}]
    result = build_portfolio_monitoring(entries)
    assert result["summary"]["statusCounts"]["no_data"] == 1
    assert result["summary"]["monitoredCount"] == 0
    assert result["projects"][0]["trend"] == {}


def test_trend_is_lender_aware():
    # current LTV higher than prior => 악화 (ceiling metric); exit rate lower => 개선
    entries = [
        {
            "projectId": "p1",
            "name": "추세딜",
            "monitoring": _monitoring(
                "watch",
                checks=[{"key": "ltv", "value": 0.70}, {"key": "exit_sale_rate", "value": 0.60}],
            ),
            "history": [
                {"createdAt": "2026-06-01T00:00:00Z", "metrics": {"ltv": 0.60, "exit_sale_rate": 0.70}},
                {"createdAt": "2026-05-01T00:00:00Z", "metrics": {"ltv": 0.55, "exit_sale_rate": 0.80}},
            ],
        }
    ]
    trend = build_portfolio_monitoring(entries)["projects"][0]["trend"]
    assert trend["ltv"]["direction"] == "악화"
    assert trend["ltv"]["from"] == 0.60  # uses the most recent prior run
    assert trend["exit_sale_rate"]["direction"] == "개선"


def test_trend_needs_both_points():
    entries = [
        {
            "projectId": "p1",
            "name": "단일딜",
            "monitoring": _monitoring("ok", checks=[{"key": "ltv", "value": 0.70}]),
            "history": None,
        }
    ]
    assert build_portfolio_monitoring(entries)["projects"][0]["trend"] == {}


def test_normalize_thresholds_drops_unknown_and_nonpositive():
    cleaned = normalize_thresholds({"ltv": 0.65, "dscr": 1.3, "bogus": 9, "sales_rate": 0, "ltc": "x"})
    assert cleaned == {"ltv": 0.65, "dscr": 1.3}


def test_portfolio_endpoint_returns_dashboard():
    client = TestClient(app)
    res = client.get("/monitoring/portfolio")
    assert res.status_code == 200
    body = res.json()
    assert body["schemaVersion"] == "realytics-portfolio-monitoring-v1"
    assert "statusCounts" in body["summary"]
    assert isinstance(body["projects"], list)
    assert isinstance(body["alertFeed"], list)


def test_set_thresholds_endpoint_persists_overrides():
    client = TestClient(app)
    projects = client.get("/projects/").json()
    project_id = projects[0]["project"]["id"]

    res = client.put(
        f"/projects/{project_id}/monitoring/thresholds",
        json={"thresholds": {"ltv": 0.65, "bogus": 1}},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["overrides"] == {"ltv": 0.65}
    assert body["effective"]["ltv"] == 0.65
    # monitoring now reflects the saved threshold
    mon = client.get(f"/projects/{project_id}/monitoring").json()
    assert mon["thresholds"]["ltv"] == 0.65
