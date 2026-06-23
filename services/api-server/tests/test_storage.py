from pathlib import Path

from storage import (
    create_user_session,
    default_state,
    get_evidence_graph_record,
    get_prompt_artifact_record,
    get_user,
    get_user_session,
    get_risk_register_record,
    list_assumption_override_records,
    list_evidence_graph_records,
    list_prompt_artifact_records,
    list_land_evidence_records,
    list_restructuring_cure_records,
    list_risk_register_records,
    list_user_activity_logs,
    load_state,
    record_user_activity_log,
    save_state,
    storage_backend,
    upsert_assumption_override_record,
    upsert_evidence_graph_record,
    upsert_prompt_artifact_record,
    upsert_land_evidence_record,
    upsert_restructuring_cure_record,
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
        evidence_graphs={"1": {"run-1": {"runId": "run-1", "summary": {"nodeCount": 2, "edgeCount": 1}}}},
        prompt_artifacts={"1": {"pa-1": {"artifactId": "pa-1", "artifactType": "ai_work_order", "body": "work order"}}},
        assumption_overrides={"1": {"ovr-1": {"overrideId": "ovr-1", "checkKey": "sales_revenue_consistency", "status": "active"}}},
        restructuring_cures={"1": {"cure-1": {"cureId": "cure-1", "cureType": "additional_collateral", "status": "active"}}},
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
    assert state["evidence_graphs"]["1"]["run-1"]["summary"]["nodeCount"] == 2
    assert state["prompt_artifacts"]["1"]["pa-1"]["body"] == "work order"
    assert state["assumption_overrides"]["1"]["ovr-1"]["checkKey"] == "sales_revenue_consistency"
    assert state["restructuring_cures"]["1"]["cure-1"]["cureType"] == "additional_collateral"
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

    evidence_graph = upsert_evidence_graph_record("1", "run-1", {
        "summary": {"nodeCount": 3, "edgeCount": 2, "riskNodeCount": 1},
        "nodes": [{"id": "risk:RISK-1", "type": "risk"}],
        "edges": [{"from": "risk:RISK-1", "to": "claim:test"}],
    })
    assert evidence_graph["nodeCount"] == 3
    assert get_evidence_graph_record("1", "run-1")["summary"]["riskNodeCount"] == 1
    assert list_evidence_graph_records("1")[0]["runId"] == "run-1"

    prompt_artifact = upsert_prompt_artifact_record("1", {
        "artifactId": "pa-work-order",
        "artifactType": "ai_work_order",
        "workflow": "full_pf_review",
        "title": "AI 업무 주문서",
        "body": "사업수지와 PF 구조를 검토하십시오.",
        "createdBy": "credit_officer",
    })
    assert prompt_artifact["artifactId"] == "pa-work-order"
    assert get_prompt_artifact_record("1", "pa-work-order")["workflow"] == "full_pf_review"
    assert list_prompt_artifact_records("1", artifact_type="ai_work_order")[0]["body"].startswith("사업수지")

    override = upsert_assumption_override_record("1", {
        "overrideId": "ovr-revenue",
        "checkKey": "sales_revenue_consistency",
        "canonicalField": "feasibility_input.total_revenue",
        "approvedValue": 100000,
        "unit": "KRW",
        "approvedBy": "credit_officer",
        "reason": "Signed workbook is authoritative.",
    })
    overrides = list_assumption_override_records("1", status="active", check_key="sales_revenue_consistency")
    assert override["overrideId"] == "ovr-revenue"
    assert overrides[0]["canonicalField"] == "feasibility_input.total_revenue"

    replacement_override = upsert_assumption_override_record("1", {
        "overrideId": "ovr-revenue-v2",
        "checkKey": "sales_revenue_consistency",
        "canonicalField": "feasibility_input.total_revenue",
        "approvedValue": 120000,
        "unit": "KRW",
        "approvedBy": "credit_officer",
        "reason": "Updated signed workbook is authoritative.",
    })
    active_overrides = list_assumption_override_records("1", status="active", check_key="sales_revenue_consistency")
    superseded_overrides = list_assumption_override_records("1", status="superseded", check_key="sales_revenue_consistency")
    assert replacement_override["overrideId"] == "ovr-revenue-v2"
    assert [item["overrideId"] for item in active_overrides] == ["ovr-revenue-v2"]
    assert superseded_overrides[0]["overrideId"] == "ovr-revenue"
    assert superseded_overrides[0]["supersededByOverrideId"] == "ovr-revenue-v2"

    revoked_override = upsert_assumption_override_record("1", {
        **replacement_override,
        "status": "revoked",
        "statusReason": "Approved value was revoked during audit review.",
        "statusChangedBy": "credit_officer",
    })
    expired_override = upsert_assumption_override_record("1", {
        "overrideId": "ovr-expired",
        "checkKey": "pf_amount_consistency",
        "canonicalField": "expected_results.total_pf_amount",
        "approvedValue": 50000,
        "status": "active",
        "expiresAt": "2000-01-01T00:00:00+00:00",
    })
    assert revoked_override["status"] == "revoked"
    assert expired_override["status"] == "active"
    assert list_assumption_override_records("1", status="expired")[0]["overrideId"] == "ovr-expired"
    assert list_assumption_override_records("1", status="active", check_key="sales_revenue_consistency") == []

    cure = upsert_restructuring_cure_record("1", {
        "cureId": "cure-unsold-ltv",
        "cureType": "additional_collateral",
        "riskCodes": ["UNSOLD_LTV_BREACH"],
        "amount": 73763000000,
        "sourceUrlOrEvidenceId": "collateral-evidence-001",
        "reviewedBy": "credit_officer",
    })
    cures = list_restructuring_cure_records("1", status="active", cure_type="additional_collateral")
    assert cure["cureId"] == "cure-unsold-ltv"
    assert cures[0]["riskCodes"] == ["UNSOLD_LTV_BREACH"]


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
    assert state["evidence_graphs"] == {}
    assert state["assumption_overrides"] == {}
    assert state["restructuring_cures"] == {}
    assert state["glossary_terms"] == {}
    assert state["glossary_term_candidates"] == {}
    assert state["project_snapshots"] == {}
    assert state["user_activity_logs"] == []
    assert state["identity"]["tenants"] == {}
    assert state["identity"]["users"] == {}
    assert state["identity"]["sessions"] == {}
