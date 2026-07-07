from storage import _deal_state_record_has_source_locator, list_deal_state_records


def test_has_source_locator_detection():
    assert _deal_state_record_has_source_locator({"sourceLocator": {"documentId": "doc-1"}}) is True
    assert _deal_state_record_has_source_locator({"sourceLocator": {"page": 0}}) is True  # 0 is a value
    assert _deal_state_record_has_source_locator({"sourceLocator": {}}) is False
    assert _deal_state_record_has_source_locator({"sourceLocator": {"documentId": None, "page": ""}}) is False
    assert _deal_state_record_has_source_locator({}) is False


def _seed(monkeypatch, tmp_path, records):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "deal-state-filters.sqlite3"))
    from storage import upsert_deal_state_record

    for rec in records:
        upsert_deal_state_record("p1", rec)


def test_missing_locator_filter(monkeypatch, tmp_path):
    _seed(
        monkeypatch,
        tmp_path,
        [
            {"recordType": "metric", "key": "m.located", "value": 1, "sourceLocator": {"cell": "B2"}},
            {"recordType": "metric", "key": "m.orphan", "value": 2, "sourceLocator": {}},
            {"recordType": "risk_issue", "key": "r.orphan", "sourceLocator": {"documentId": None}},
        ],
    )

    missing = list_deal_state_records("p1", missing_locator=True)
    assert {r["key"] for r in missing} == {"m.orphan", "r.orphan"}

    located = list_deal_state_records("p1", missing_locator=False)
    assert {r["key"] for r in located} == {"m.located"}

    # No filter returns everything.
    assert len(list_deal_state_records("p1")) == 3


def test_review_status_filter(monkeypatch, tmp_path):
    _seed(
        monkeypatch,
        tmp_path,
        [
            {"recordType": "metric", "key": "m.approved", "value": 1, "reviewStatus": "approved", "sourceLocator": {"cell": "A1"}},
            {"recordType": "metric", "key": "m.new", "value": 2, "sourceLocator": {"cell": "A2"}},
        ],
    )

    approved = list_deal_state_records("p1", review_status="approved")
    assert {r["key"] for r in approved} == {"m.approved"}

    unreviewed = list_deal_state_records("p1", review_status="unreviewed")
    assert {r["key"] for r in unreviewed} == {"m.new"}


def test_endpoint_completeness_summary(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from main import app

    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "deal-state-endpoint.sqlite3"))
    from storage import upsert_deal_state_record

    client = TestClient(app)
    created = client.post("/projects/", json={"project": {"name": "필터 테스트"}}).json()
    pid = created.get("id") or created["project"]["id"]

    for rec in [
        {"recordType": "metric", "key": "m1", "value": 1, "sourceLocator": {"cell": "A1"}},
        {"recordType": "metric", "key": "m2", "value": 2, "sourceLocator": {}},
        {"recordType": "risk_issue", "key": "r1", "sourceLocator": {}},
    ]:
        upsert_deal_state_record(pid, rec)

    res = client.get(f"/projects/{pid}/deal-state-records", params={"missing_locator": "true"})
    assert res.status_code == 200
    body = res.json()
    keys = {r["key"] for r in body["records"]}
    assert keys == {"m2", "r1"}
    # Summary is over the full scope, not the filtered view.
    summary = body["completenessSummary"]
    assert summary["scopeCount"] == 3
    assert summary["missingSourceLocatorCount"] == 2
    assert summary["withSourceLocatorCount"] == 1
    assert summary["unreviewedCount"] == 3
