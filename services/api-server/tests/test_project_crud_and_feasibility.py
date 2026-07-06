from fastapi.testclient import TestClient

from main import app
from routers import projects
from storage import (
    get_risk_register_record,
    list_deal_state_records,
    upsert_chief_pod_artifact_record,
    upsert_evidence_graph_record,
    upsert_risk_register_record,
)


def test_project_crud_create_update_delete_cascades_runtime_records(monkeypatch):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    client = TestClient(app)

    created = client.post(
        "/projects/",
        json={
            "project": {"name": "CRUD QA Project", "type": "mixed_use"},
            "feasibility_input": {"total_revenue": 1000, "total_cost": 800},
        },
    )
    assert created.status_code == 200
    body = created.json()
    project_id = body["project"]["id"]
    assert body["id"] == project_id
    assert body["project"]["name"] == "CRUD QA Project"
    assert body["project"]["status"] == "draft"
    assert body["_meta"]["created_at"]

    fetched = client.get(f"/projects/{project_id}")
    assert fetched.status_code == 200
    assert fetched.json()["project"]["id"] == project_id

    updated = client.put(
        f"/projects/{project_id}",
        json={"project": {"status": "pf_review"}, "land": {"address": "Seoul"}},
    )
    assert updated.status_code == 200
    updated_body = updated.json()
    assert updated_body["project"]["status"] == "pf_review"
    assert updated_body["land"]["address"] == "Seoul"

    projects.REPORTS_DB[project_id] = [{"report_id": "r-1"}]
    projects.ORCHESTRATIONS_DB[project_id] = {"run-1": {"status": "completed"}}
    projects.MARKET_EVIDENCE_DB[project_id] = {"run-1:ev-1": {"evidenceId": "ev-1"}}
    projects.PROJECT_SNAPSHOTS_DB[project_id] = {"snapshot-run-1": {"runId": "run-1"}}
    deleted = client.delete(f"/projects/{project_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert project_id not in projects.PROJECTS_DB
    assert project_id not in projects.REPORTS_DB
    assert project_id not in projects.ORCHESTRATIONS_DB
    assert project_id not in projects.MARKET_EVIDENCE_DB
    assert project_id not in projects.PROJECT_SNAPSHOTS_DB
    assert client.get(f"/projects/{project_id}").status_code == 404


def test_apply_parsed_data_preserves_existing_project_identity(monkeypatch):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    client = TestClient(app)
    created = client.post(
        "/projects/",
        json={
            "project": {"name": "Upload Identity Project", "type": "mixed_use"},
            "land": {"address": "기존 주소"},
        },
    ).json()
    project_id = created["project"]["id"]

    result = projects.project_repository().apply_parsed_data(
        project_id,
        {
            "project": {"id": project_id},
            "land": {"pnu": "2729011700101230004"},
        },
    )

    assert result is not None
    assert result["project"]["name"] == "Upload Identity Project"
    assert result["project"]["type"] == "mixed_use"
    assert result["land"]["address"] == "기존 주소"
    assert result["land"]["pnu"] == "2729011700101230004"


def test_feasibility_calculate_endpoint_uses_project_defaults_and_overrides(monkeypatch):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    client = TestClient(app)
    created = client.post(
        "/projects/",
        json={
            "project": {"name": "Feasibility QA Project"},
            "feasibility_input": {
                "total_revenue": 1000,
                "total_cost": 700,
                "finance_cost": 40,
            },
        },
    ).json()
    project_id = created["project"]["id"]

    base_result = client.post(f"/projects/{project_id}/feasibility/calculate", json={})
    assert base_result.status_code == 200
    assert base_result.json()["development_profit"] == 300
    assert base_result.json()["status"] == "strong"

    override_result = client.post(
        f"/projects/{project_id}/feasibility/calculate",
        json={"total_cost": 950},
    )
    assert override_result.status_code == 200
    assert override_result.json()["development_profit"] == 50
    assert override_result.json()["status"] in {"weak", "thin"}

    client.delete(f"/projects/{project_id}")


def test_calculation_run_registry_persists_metrics(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "calculation-registry.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    client = TestClient(app)
    created = client.post(
        "/projects/",
        json={
            "project": {"name": "Calculation Registry Project"},
            "feasibility_input": {
                "total_revenue": 100000000000,
                "total_cost": 80000000000,
                "finance_cost": 5000000000,
            },
        },
    ).json()
    project_id = created["project"]["id"]

    res = client.post(
        f"/projects/{project_id}/pf/structure",
        json={
            "tranches": [{"name": "senior", "amount": 50000000000}],
            "total_revenue": 100000000000,
            "total_cost": 80000000000,
            "finance_cost": 5000000000,
            "inputEvidenceIds": ["evidence:unit-test"],
            "assumptionIds": ["assumption:unit-test"],
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["calculationRunId"]

    runs_res = client.get(f"/projects/{project_id}/calculation-runs?engine_key=pf_metrics")
    assert runs_res.status_code == 200
    runs = runs_res.json()["records"]
    assert any(run["calculationRunId"] == body["calculationRunId"] for run in runs)

    metrics_res = client.get(
        f"/projects/{project_id}/deal-state-records?record_type=metric&calculation_run_id={body['calculationRunId']}"
    )
    assert metrics_res.status_code == 200
    metric_keys = {row["key"] for row in metrics_res.json()["records"]}
    assert "pf_metrics.ltv" in metric_keys

    evidence_res = client.get(
        f"/projects/{project_id}/deal-state-records?record_type=evidence&calculation_run_id={body['calculationRunId']}"
    )
    assert evidence_res.status_code == 200
    assert evidence_res.json()["records"][0]["key"] == "evidence:unit-test"

    client.delete(f"/projects/{project_id}")


def test_deal_state_records_materialize_from_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "deal-state-artifacts.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    project_id = "artifact-materialization-project"

    upsert_risk_register_record(
        project_id,
        "run-risk",
        {
            "registerId": "risk-register-test",
            "risks": [
                {
                    "code": "TEST_RISK",
                    "severity": "high",
                    "sourceLocator": {"documentId": "doc-1", "page": 3},
                }
            ],
        },
    )
    upsert_evidence_graph_record(
        project_id,
        "run-graph",
        {
            "graphId": "graph-test",
            "nodes": [
                {
                    "id": "source-1",
                    "type": "source_locator",
                    "label": "사업계획서 p.3",
                    "metadata": {"documentId": "doc-1", "page": 3},
                },
                {
                    "id": "ltv-node",
                    "type": "metric",
                    "metadata": {"key": "ltv", "value": 0.62, "unit": "ratio"},
                },
            ],
            "edges": [],
        },
    )
    upsert_chief_pod_artifact_record(
        project_id,
        {
            "artifactKey": "artifact-pf-credit-test",
            "podKey": "pf_credit",
            "artifactType": "pod_result",
            "result": {
                "metrics": {"seniorRecoveryRatio": 0.98},
                "riskFlags": [{"code": "ARTIFACT_RISK", "severity": "medium"}],
                "evidenceIds": ["evidence:auto-supplement"],
                "assumptionIds": ["assumption:base-case"],
            },
        },
    )

    risks = list_deal_state_records(project_id, record_type="risk_issue")
    risk_keys = {record["key"] for record in risks}
    assert {"TEST_RISK", "ARTIFACT_RISK"}.issubset(risk_keys)
    assert next(record for record in risks if record["key"] == "TEST_RISK")["sourceLocator"]["page"] == 3

    metrics = list_deal_state_records(project_id, record_type="metric")
    metric_keys = {record["key"] for record in metrics}
    assert "ltv" in metric_keys
    assert "pf_credit.seniorRecoveryRatio" in metric_keys

    evidence = list_deal_state_records(project_id, record_type="evidence")
    evidence_keys = {record["key"] for record in evidence}
    assert {"source-1", "evidence:auto-supplement"}.issubset(evidence_keys)

    assumptions = list_deal_state_records(project_id, record_type="assumption")
    assert assumptions[0]["key"] == "assumption:base-case"


def test_feasibility_workbook_approve_persists_canonical_v2(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "approval.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    client = TestClient(app)
    created = client.post("/projects/", json={"project": {"name": "Import Approval Project"}}).json()
    project_id = created["project"]["id"]
    draft = {
        "version": "realytics-feasibility-study-v2",
        "project_id": project_id,
        "project_name": "Import Approval Project",
        "asset_type": "mixed_use",
        "metrics": {
            "total_revenue": 100000000000,
            "total_cost": 85000000000,
            "development_profit": 15000000000,
            "profit_margin": 0.15,
            "ltv": 0.6,
        },
        "financing": [
            {
                "tranche_id": "tr_a",
                "name": "Tr.A",
                "priority": "senior",
                "amount": 60000000000,
            }
        ],
    }

    approved = client.post(
        f"/projects/{project_id}/feasibility-workbook/approve",
        json={
            "importId": "fs-import-test",
            "approvedBy": "credit-officer",
            "comment": "Use signed workbook as canonical feasibility.",
            "feasibilityStudyV2Draft": draft,
        },
    )

    assert approved.status_code == 200
    body = approved.json()
    assert body["canonicalFeasibilityStudyV2"]["raw_import"]["canonicalStatus"] == "approved"
    assert body["feasibilityInput"]["total_revenue"] == 100000000000
    fetched = client.get(f"/projects/{project_id}").json()
    assert fetched["feasibilityStudyV2"]["project_name"] == "Import Approval Project"
    assert fetched["_meta"]["latestFeasibilityWorkbookImportId"] == "fs-import-test"

    client.delete(f"/projects/{project_id}")


def test_risk_report_workbook_approve_persists_register_and_sections(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "risk-approval.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    client = TestClient(app)
    created = client.post("/projects/", json={"project": {"name": "Risk Workbook Project"}}).json()
    project_id = created["project"]["id"]
    draft = {
        "schemaVersion": "realytics-risk-report-xlsx-v1",
        "importId": "risk-import-test",
        "validation": {"status": "passed", "importReady": True},
        "sourceDocument": {"originalFileName": "risk-report.xlsx"},
        "metrics": {"totalRevenue": 1655.97, "pfLoan": 900},
        "riskRegisterDraft": {
            "schemaVersion": "realytics-risk-register-v1",
            "status": "imported_pending_review",
            "risks": [
                {
                    "riskId": "RR-XLSX-001",
                    "code": "RISK_REPORT_XLSX_001",
                    "severity": "critical",
                    "reviewStatus": "pending_review",
                }
            ],
        },
        "reportSectionsDraft": {
            "paragraphs": [
                {
                    "section": "Cash Flow",
                    "category": "요약",
                    "text": "현금흐름 검토 문구입니다.",
                }
            ],
            "approvalConditions": [
                {
                    "section": "승인조건",
                    "category": "금융",
                    "text": "PF 한도 보강 조건입니다.",
                }
            ],
        },
    }

    approved = client.post(
        f"/projects/{project_id}/risk-report-workbook/approve",
        json={
            "approvedBy": "risk-reviewer",
            "comment": "Approved for Risk Register and report section import.",
            "riskReportImportDraft": draft,
        },
    )

    assert approved.status_code == 200
    body = approved.json()
    assert body["riskRegister"]["riskCount"] == 1
    assert body["reportSections"]["paragraphs"][0]["text"] == "현금흐름 검토 문구입니다."
    assert get_risk_register_record(project_id, "risk-import-test")["risks"][0]["riskId"] == "RR-XLSX-001"
    fetched = client.get(f"/projects/{project_id}").json()
    assert fetched["riskReportSections"]["importApproval"]["approvedBy"] == "risk-reviewer"
    assert fetched["_meta"]["latestRiskReportWorkbookImportId"] == "risk-import-test"

    client.delete(f"/projects/{project_id}")


def test_report_template_editor_api_persists_project_template(monkeypatch):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    client = TestClient(app)
    created = client.post("/projects/", json={"project": {"name": "Template Editor Project"}}).json()
    project_id = created["project"]["id"]

    upsert = client.post(
        f"/projects/{project_id}/report-templates",
        json={
            "templateId": "phase11-custom",
            "name": "Custom Phase 11 Template",
            "editedBy": "report-editor",
            "sections": [
                {
                    "sectionKey": "marketability_analysis",
                    "title": "분양성 검토",
                    "sourceAgents": ["MarketabilityAgent"],
                    "templateText": "분양가와 흡수율을 근거자료와 함께 기재한다.",
                }
            ],
        },
    )

    assert upsert.status_code == 200
    body = upsert.json()
    assert body["template"]["templateId"] == "phase11-custom"
    assert body["template"]["sections"][0]["title"] == "분양성 검토"
    assert body["template"]["version"] == 1

    listed = client.get(f"/projects/{project_id}/report-templates")
    assert listed.status_code == 200
    assert listed.json()["templates"][0]["templateId"] == "phase11-custom"

    fetched = client.get(f"/projects/{project_id}/report-templates/phase11-custom")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Custom Phase 11 Template"

    updated = client.post(
        f"/projects/{project_id}/report-templates",
        json={
            "templateId": "phase11-custom",
            "name": "Custom Phase 11 Template v2",
            "editedBy": "report-editor",
            "changeMemo": "Update wording",
            "sections": ["cover", "marketability_analysis"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["template"]["version"] == 2
    assert updated.json()["template"]["history"][0]["version"] == 1

    client.delete(f"/projects/{project_id}")


def test_report_generation_applies_saved_report_template(monkeypatch):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    client = TestClient(app)
    created = client.post(
        "/projects/",
        json={
            "project": {"name": "Templated Report Project", "type": "mixed_use"},
            "feasibility_input": {"total_revenue": 1000, "total_cost": 800},
            "pf_structure": [{"tranche": "Tr.A", "amount": 500, "priority": 1}],
        },
    ).json()
    project_id = created["project"]["id"]

    template = client.post(
        f"/projects/{project_id}/report-templates",
        json={
            "templateId": "phase11-template-apply-test",
            "name": "Template Apply Test",
            "editedBy": "report-editor",
            "sections": [
                {
                    "sectionKey": "cover",
                    "title": "표지",
                    "enabled": True,
                    "sourceAgents": ["ParserAgent"],
                    "requiredEvidence": ["사업계획서"],
                    "templateText": "표지 문구",
                },
                {
                    "sectionKey": "marketability_analysis",
                    "title": "분양성 검토",
                    "enabled": True,
                    "sourceAgents": ["MarketabilityAnalysisAgent"],
                    "requiredEvidence": ["분양가 비교사례"],
                    "templateText": "분양성 문구",
                },
                {
                    "sectionKey": "custom_missing_section",
                    "title": "미매핑 사용자 섹션",
                    "enabled": True,
                    "sourceAgents": ["CreditReviewAgent"],
                },
                {
                    "sectionKey": "eod",
                    "title": "EOD",
                    "enabled": False,
                },
            ],
        },
    ).json()["template"]

    generated = client.post(
        f"/projects/{project_id}/reports/generate",
        json={"templateId": template["templateId"]},
    )

    assert generated.status_code == 200
    body = generated.json()
    report = body["report"]
    assert report["appliedTemplate"]["templateId"] == "phase11-template-apply-test"
    assert report["templateApplication"]["unmappedSections"][0]["templateSectionKey"] == "custom_missing_section"
    assert report["templateApplication"]["disabledSections"] == ["eod"]
    assert report["sectionOrder"][0]["title"] == "표지"
    assert report["sectionOrder"][1]["key"] == "marketAnalysis"
    assert report["sectionOrder"][1]["requiredEvidence"] == ["분양가 비교사례"]
    assert body["metadata"]["applied_template_id"] == "phase11-template-apply-test"
    assert "## 적용 보고서 템플릿" in body["report_markdown"]

    client.delete(f"/projects/{project_id}")
