from storage import (
    list_deal_state_records,
    upsert_chief_pod_artifact_record,
)


def test_nested_chief_artifact_materializes_metrics_risks_and_references(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "materialize.sqlite3"))
    project_id = "materialize-project"

    upsert_chief_pod_artifact_record(
        project_id,
        {
            "artifactKey": "artifact:pf_credit:test",
            "podKey": "pf_credit",
            "artifactType": "pf_credit_pod_result",
            "result": {
                "scenariosEvaluation": {
                    "waterfall": {
                        "summary": {"seniorRecoveryRatio": 0.96},
                        "riskIssues": [
                            {
                                "code": "SENIOR_SHORTFALL",
                                "severity": "critical",
                                "sourceLocator": {"calculationRunId": "calc:waterfall:1"},
                            }
                        ],
                    }
                },
                "inputEvidenceIds": ["evidence:term-sheet:1"],
                "assumptionIds": ["assumption:discount-rate:1"],
            },
        },
    )

    records = list_deal_state_records(project_id, limit=100)
    by_key = {record["key"]: record for record in records}

    metric = by_key["pf_credit.scenariosEvaluation.waterfall.summary.seniorRecoveryRatio"]
    assert metric["value"] == 0.96
    assert metric["sourceLocator"]["artifactPath"].endswith("seniorRecoveryRatio")
    assert by_key["SENIOR_SHORTFALL"]["recordType"] == "risk_issue"
    assert by_key["SENIOR_SHORTFALL"]["sourceLocator"]["calculationRunId"] == "calc:waterfall:1"
    assert by_key["evidence:term-sheet:1"]["recordType"] == "evidence"
    assert by_key["assumption:discount-rate:1"]["recordType"] == "assumption"
