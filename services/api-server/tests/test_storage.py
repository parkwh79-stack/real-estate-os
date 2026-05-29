from pathlib import Path

from storage import (
    create_user_session,
    default_state,
    get_user,
    get_user_session,
    get_risk_register_record,
    list_land_evidence_records,
    list_risk_register_records,
    list_user_activity_logs,
    load_state,
    record_user_activity_log,
    save_state,
    storage_backend,
    upsert_land_evidence_record,
    upsert_risk_register_record,
    upsert_user,
)


def test_json_state_storage_round_trip(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("REALYTICS_STATE_FILE", str(state_path))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "json")

    save_state(
        projects={"1": {"project": {"id": "1", "name": "Stored Project"}}},
        reports={"1": [{"report_id": "r-1"}]},
        orchestrations={"1": {"run-1": {"status": "completed"}}},
        project_id_counter=2,
        market_evidence={"1": {"run-1:ev-1": {"recordKey": "run-1:ev-1", "evidenceId": "ev-1"}}},
        land_evidence={"1": {"official_land_price:pnu-1": {"recordKey": "official_land_price:pnu-1", "pnu": "pnu-1"}}},
        risk_registers={"1": {"run-1": {"runId": "run-1", "status": "blocking", "risks": [{"riskId": "R-1"}]}}},
        project_snapshots={"1": {"snapshot-run-1": {"snapshotId": "snapshot-run-1", "runId": "run-1"}}},
        user_activity_logs=[{"eventId": "evt-1", "eventType": "login", "userId": "u-1"}],
    )

    state = load_state()

    assert state["project_id_counter"] == 2
    assert state["projects"]["1"]["project"]["name"] == "Stored Project"
    assert state["reports"]["1"][0]["report_id"] == "r-1"
    assert state["orchestrations"]["1"]["run-1"]["status"] == "completed"
    assert state["market_evidence"]["1"]["run-1:ev-1"]["evidenceId"] == "ev-1"
    assert state["land_evidence"]["1"]["official_land_price:pnu-1"]["pnu"] == "pnu-1"
    assert state["risk_registers"]["1"]["run-1"]["status"] == "blocking"
    assert state["project_snapshots"]["1"]["snapshot-run-1"]["runId"] == "run-1"
    assert state["user_activity_logs"][0]["eventId"] == "evt-1"
    assert state["identity"]["users"] == {}
    assert Path(state_path).exists()


def test_sqlite_state_storage_round_trip(tmp_path, monkeypatch):
    db_path = tmp_path / "realytics.sqlite3"
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(db_path))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")

    save_state(
        projects={"1": {"project": {"id": "1", "name": "SQLite Project"}, "_meta": {"updated_at": "now"}}},
        reports={"1": [{"report_id": "r-1", "version": 1, "created_at": "now"}]},
        orchestrations={"1": {"run-1": {"run_id": "run-1", "status": "completed", "started_at": "now"}}},
        project_id_counter=3,
        market_evidence={
            "1": {
                "run-1:ev-1": {
                    "recordKey": "run-1:ev-1",
                    "runId": "run-1",
                    "evidenceId": "ev-1",
                    "evidenceType": "sale_comparable",
                    "validationStatus": "pass",
                    "createdAt": "now",
                }
            }
        },
        project_snapshots={
            "1": {
                "snapshot-run-1": {
                    "snapshotId": "snapshot-run-1",
                    "runId": "run-1",
                    "createdAt": "now",
                }
            }
        },
    )

    state = load_state()

    assert storage_backend() == "sqlite"
    assert state["project_id_counter"] == 3
    assert state["projects"]["1"]["project"]["name"] == "SQLite Project"
    assert state["reports"]["1"][0]["report_id"] == "r-1"
    assert state["orchestrations"]["1"]["run-1"]["status"] == "completed"
    assert state["market_evidence"]["1"]["run-1:ev-1"]["evidenceType"] == "sale_comparable"
    assert state["project_snapshots"]["1"]["snapshot-run-1"]["runId"] == "run-1"
    assert Path(db_path).exists()

    record = record_user_activity_log({
        "eventType": "api_request",
        "userId": "u-1",
        "projectId": "1",
        "method": "GET",
        "path": "/projects/1",
        "statusCode": 200,
    })
    logs = list_user_activity_logs(user_id="u-1", project_id="1")

    assert record["eventId"]
    assert logs[0]["userId"] == "u-1"
    assert logs[0]["projectId"] == "1"

    user = upsert_user({
        "userId": "u-identity",
        "tenantId": "tenant-1",
        "email": "identity@example.com",
        "displayName": "Identity User",
        "role": "reviewer",
    })
    session = create_user_session({
        "userId": "u-identity",
        "tenantId": "tenant-1",
        "provider": "local-test",
    })

    assert user["role"] == "reviewer"
    assert get_user("u-identity")["tenantId"] == "tenant-1"
    assert get_user_session(session["sessionId"])["userId"] == "u-identity"

    land_record = upsert_land_evidence_record("1", {
        "evidenceType": "official_land_price",
        "pnu": "2729011700101230004",
        "pricePerSqm": 1500000,
        "baseYear": 2026,
        "sourceName": "manual-test",
    })
    land_records = list_land_evidence_records("1", pnu="2729011700101230004")
    assert land_record["recordKey"]
    assert land_records[0]["pricePerSqm"] == 1500000

    risk_register = upsert_risk_register_record("1", "run-1", {
        "status": "blocking",
        "risks": [{"riskId": "RISK-1", "severity": "critical"}],
    })
    assert risk_register["riskCount"] == 1
    assert get_risk_register_record("1", "run-1")["risks"][0]["riskId"] == "RISK-1"
    assert list_risk_register_records("1")[0]["runId"] == "run-1"


def test_sqlite_state_load_migrates_legacy_json_state(tmp_path, monkeypatch):
    json_path = tmp_path / "state.json"
    db_path = tmp_path / "realytics.sqlite3"
    json_path.write_text(
        """
        {
          "version": 1,
          "project_id_counter": 4,
          "projects": {"1": {"project": {"id": "1", "name": "Legacy JSON"}}},
          "reports": {},
          "orchestrations": {}
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("REALYTICS_STATE_FILE", str(json_path))
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(db_path))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")

    state = load_state()

    assert state["projects"]["1"]["project"]["name"] == "Legacy JSON"
    assert state["project_id_counter"] == 4
    assert Path(db_path).exists()


def test_default_state_shape():
    state = default_state()

    assert state["projects"] == {}
    assert state["reports"] == {}
    assert state["orchestrations"] == {}
    assert state["market_evidence"] == {}
    assert state["land_evidence"] == {}
    assert state["risk_registers"] == {}
    assert state["project_snapshots"] == {}
    assert state["user_activity_logs"] == []
    assert state["identity"]["tenants"] == {}
    assert state["identity"]["users"] == {}
    assert state["identity"]["sessions"] == {}
