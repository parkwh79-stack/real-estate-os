import pytest
from fastapi.testclient import TestClient

from db.repositories.upload_archive_repository import UploadArchiveRepository
from main import app
from routers import projects
from use_cases.workbook_import_approval import feasibility_study_draft_hash
from storage import (
    get_risk_register_record,
    list_deal_state_records,
    upsert_chief_pod_artifact_record,
    upsert_deal_state_record,
    upsert_evidence_graph_record,
    upsert_risk_register_record,
    upsert_user,
)


SERVER_MANAGED_PROJECT_FIELDS = (
    "feasibility_input_sources",
    "_meta",
    "source_documents",
    "sourceDocuments",
    "canonicalFeasibilityStudyV2",
    "canonical_feasibility_study_v2",
    "feasibilityStudyV2",
    "feasibility_study_v2",
    "feasibilityStudyV2Draft",
    "feasibility_study_v2_draft",
    "approvedInputSnapshot",
    "approved_input_snapshot",
    "buildableEnvelope",
    "buildable_envelope",
    "regulatoryConstraintSnapshot",
    "regulatory_constraint_snapshot",
    "legalPermitReview",
    "legalReview",
    "legal_review",
    "permitReview",
    "permit_review",
    "legal_permit_review",
)


@pytest.mark.parametrize("path", ["/projects", "/projects/"])
@pytest.mark.parametrize("role", ["admin", "reviewer"])
def test_project_collection_preserves_auth_without_redirect(path, role, monkeypatch):
    """Next normalizes the BFF path; an upstream redirect loses its bearer."""
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "strict")
    monkeypatch.setenv("REALYTICS_TENANT_ISOLATION_MODE", "strict")
    user_id = f"collection-path-{role}"
    upsert_user({
        "userId": user_id,
        "tenantId": "collection-path-test",
        "role": role,
        "status": "active",
    })
    client = TestClient(app, follow_redirects=False)
    headers = {"X-REALYTICS-USER-ID": user_id}
    response = client.post(path, headers=headers, json={"project": {"name": "Collection route QA"}})
    assert "location" not in response.headers
    assert response.status_code == (200 if role == "admin" else 403)
    if role == "admin":
        project_id = response.json()["project"]["id"]
        try:
            listed = client.get(path, headers=headers)
            assert listed.status_code == 200
            assert "location" not in listed.headers
            assert any(row["project"]["id"] == project_id for row in listed.json())
        finally:
            client.delete(f"/projects/{project_id}", headers=headers)
    denied = client.post(path, json={"project": {"name": "Anonymous must be denied"}})
    assert denied.status_code == 403
    assert "location" not in denied.headers


def _isolated_upload_archive(tmp_path, monkeypatch):
    repository = UploadArchiveRepository(
        api_server_root=tmp_path,
        archive_root=tmp_path / "uploads",
    )
    monkeypatch.setattr(projects, "upload_archive_repository", lambda: repository)
    return repository


def _validated_workbook_source_record(draft, archive_meta, *, status="passed", blocking_formula_errors=False):
    source_hash = archive_meta["archiveId"]
    validation = {
        "status": status,
        "profile": "legacy-bonri-mixed-use-pf-feasibility",
        "sourceDocumentHash": source_hash,
        "formulaIntegrity": {
            "hasBlockingFormulaErrors": blocking_formula_errors,
            "hasExternalWorkbookReferences": False,
        },
        "requiredSheetChecks": [{"sheetName": "사업수지", "present": True}],
        "requiredKeyCellChecks": [{"key": "total_revenue", "passed": True}],
        "reconciliationChecks": [{"name": "profit", "passed": True}],
        "sourceConflicts": [],
    }
    source = {
        **archive_meta,
        "originalFileName": "server-validated-feasibility.xlsx",
        "originalFileHash": source_hash,
        "documentType": "feasibility_workbook",
        "validationStatus": status,
        "validation": validation,
        "feasibilityStudyV2DraftHash": feasibility_study_draft_hash(draft),
    }
    return source, validation


def _server_validated_workbook_source(project_id, draft, *, status="passed", blocking_formula_errors=False):
    workbook_bytes = (
        f"REALYTICS dummy feasibility workbook\n"
        f"project={project_id}\n"
        f"draft={feasibility_study_draft_hash(draft)}\n"
    ).encode("utf-8")
    archive_meta = projects.upload_archive_repository().archive_upload(
        project_id,
        workbook_bytes,
        "server-validated-feasibility.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    source, validation = _validated_workbook_source_record(
        draft,
        archive_meta,
        status=status,
        blocking_formula_errors=blocking_formula_errors,
    )
    projects.source_document_repository().save_upload_metadata(project_id, source)
    return source, validation


@pytest.mark.parametrize("field_name", SERVER_MANAGED_PROJECT_FIELDS)
def test_project_create_rejects_server_managed_authority_fields(field_name, monkeypatch):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    response = TestClient(app).post(
        "/projects/",
        json={
            "project": {"name": "Authority Injection Create"},
            field_name: {"attackerControlled": True},
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "server_managed_project_fields"
    assert response.json()["detail"]["fields"] == [field_name]


@pytest.mark.parametrize("field_name", SERVER_MANAGED_PROJECT_FIELDS)
def test_project_update_rejects_server_managed_authority_fields(field_name, monkeypatch):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    client = TestClient(app)
    project_id = client.post(
        "/projects/",
        json={"project": {"name": "Authority Injection Update"}},
    ).json()["project"]["id"]

    response = client.put(
        f"/projects/{project_id}",
        json={field_name: {"attackerControlled": True}},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "server_managed_project_fields"
    assert response.json()["detail"]["fields"] == [field_name]
    persisted = client.get(f"/projects/{project_id}").json()
    if field_name == "_meta":
        assert "attackerControlled" not in persisted["_meta"]
    else:
        assert field_name not in persisted
    client.delete(f"/projects/{project_id}")


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


def test_project_list_contract_marks_missing_project_type_as_unknown(monkeypatch):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    client = TestClient(app)
    created = client.post("/projects/", json={"project": {"name": "Type Pending Project"}}).json()
    project_id = created["project"]["id"]

    assert created["project"]["type"] == "unknown"
    listed = next(item for item in client.get("/projects/").json() if item["project"]["id"] == project_id)
    assert listed["project"]["type"] == "unknown"

    client.delete(f"/projects/{project_id}")


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

    runs = client.get(f"/projects/{project_id}/calculation-runs?engine_key=feasibility").json()["records"]
    by_id = {run["calculationRunId"]: run for run in runs}
    baseline = by_id[base_result.json()["calculationRunId"]]
    changed = by_id[override_result.json()["calculationRunId"]]
    assert baseline["inputSnapshot"] == {"total_revenue": 1000, "total_cost": 700, "finance_cost": 40}
    assert changed["inputSnapshot"] == {"total_revenue": 1000, "total_cost": 950, "finance_cost": 40}
    assert changed["resultSnapshot"]["development_profit"] == changed["inputSnapshot"]["total_revenue"] - changed["inputSnapshot"]["total_cost"]
    assert projects.project_repository().get(project_id)["feasibility_input"]["total_cost"] == 700

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


def test_deal_state_record_review_api_updates_risk_issue(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "deal-state-review.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    client = TestClient(app)
    created = client.post("/projects/", json={"project": {"name": "Deal State Review Project"}}).json()
    project_id = created["project"]["id"]
    upsert_risk_register_record(
        project_id,
        "run-review",
        {
            "risks": [
                {
                    "code": "SENIOR_SHORTFALL",
                    "severity": "critical",
                    "reviewStatus": "open",
                    "sourceLocator": {"artifactKey": "scenario-test"},
                }
            ],
        },
    )
    risk = next(
        record
        for record in list_deal_state_records(project_id, record_type="risk_issue")
        if record["key"] == "SENIOR_SHORTFALL"
    )
    upsert_user(
        {
            "userId": "credit_officer",
            "tenantId": "deal-state-review-tenant",
            "role": "reviewer",
            "status": "active",
        }
    )

    res = client.patch(
        f"/projects/{project_id}/deal-state-records/{risk['recordId']}/review",
        json={
            "reviewStatus": "resolved",
            "reviewedBy": "spoofed-credit-officer",
            "comment": "Resolved by senior reduction cure.",
        },
        headers={"X-REALYTICS-USER-ID": "credit_officer"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["record"]["reviewStatus"] == "resolved"
    assert body["record"]["reviewedBy"] == "credit_officer"
    assert body["record"]["reviewHistory"][0]["reviewStatus"] == "resolved"

    reviewed = next(
        record
        for record in list_deal_state_records(project_id, record_type="risk_issue")
        if record["key"] == "SENIOR_SHORTFALL"
    )
    assert reviewed["reviewStatus"] == "resolved"

    client.delete(f"/projects/{project_id}")


def test_report_generation_persists_paragraph_evidence_links(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "paragraph-links.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    client = TestClient(app)
    created = client.post(
        "/projects/",
        json={
            "project": {"name": "Paragraph Link Project", "type": "mixed_use"},
            "land": {"address": "Seoul"},
            "feasibility_input": {"total_revenue": 100000000000, "total_cost": 80000000000},
        },
    ).json()
    project_id = created["project"]["id"]
    land_evidence = upsert_deal_state_record(
        project_id,
        {
            "recordType": "evidence",
            "key": "land.official_land_price",
            "label": "Official land price evidence",
            "sourceType": "public_api",
            "sourceLocator": {"apiRowId": "pnu-1111010100100010001", "evidenceId": "land-price-1"},
            "confidence": 0.95,
        },
    )
    pf_metric = upsert_deal_state_record(
        project_id,
        {
            "recordType": "metric",
            "key": "pf_metrics.ltv",
            "label": "LTV",
            "value": 0.62,
            "unit": "ratio",
            "sourceType": "calculation_run",
            "calculationRunId": "calc-pf-test",
            "sourceLocator": {"artifactKey": "pf-structure-test"},
        },
    )
    risk = upsert_deal_state_record(
        project_id,
        {
            "recordType": "risk_issue",
            "key": "SENIOR_SHORTFALL",
            "severity": "critical",
            "reviewStatus": "resolved",
            "sourceType": "risk_register",
            "sourceLocator": {"artifactKey": "scenario-stress"},
        },
    )

    res = client.post(
        f"/projects/{project_id}/reports/generate",
        json={"options": {"format": "json+markdown"}},
    )

    assert res.status_code == 200
    body = res.json()
    links = body["report"]["paragraphEvidenceLinks"]
    assert links["schemaVersion"] == "realytics-paragraph-evidence-links-v1"
    assert links["sectionCount"] >= 1
    assert body["paragraphEvidenceLinks"]["linkedRecordCount"] >= 2
    assert body["metadata"]["paragraph_evidence_link_count"] == links["sectionCount"]

    land_link = next(item for item in links["links"] if item["sectionKey"] == "landSecuring")
    pf_link = next(item for item in links["links"] if item["sectionKey"] in {"financialTerms", "businessFeasibility"})
    risk_link = next(item for item in links["links"] if item["sectionKey"] in {"exitStrategy", "eodTriggers", "drawdownConditions"})

    assert land_evidence["recordId"] in land_link["evidenceRecordIds"]
    assert pf_metric["recordId"] in pf_link["metricRecordIds"]
    assert risk["recordId"] in risk_link["riskIssueRecordIds"]

    client.delete(f"/projects/{project_id}")


def test_source_locator_audit_persists_traceability_risks(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "source-locator-audit.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    client = TestClient(app)
    created = client.post("/projects/", json={"project": {"name": "Source Locator Audit Project"}}).json()
    project_id = created["project"]["id"]

    upsert_deal_state_record(
        project_id,
        {
            "recordType": "metric",
            "key": "cashflow.maxMinus",
            "value": -10000000000,
            "sourceType": "calculation_run",
            "sourceLocator": {},
        },
    )
    upsert_deal_state_record(
        project_id,
        {
            "recordType": "metric",
            "key": "pf_metrics.ltv",
            "value": 0.62,
            "sourceType": "calculation_run",
            "sourceLocator": {"calculationRunId": "calc-only-1", "engineKey": "pf_metrics"},
        },
    )
    upsert_deal_state_record(
        project_id,
        {
            "recordType": "evidence",
            "key": "land.official_land_price",
            "sourceType": "public_api",
            "sourceLocator": {"apiRowId": "vworld-row-1"},
        },
    )

    res = client.post(
        f"/projects/{project_id}/deal-state-records/source-locator-audit",
        headers={"X-REALYTICS-USER-ID": "auditor"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["missingLocatorCount"] == 1
    assert body["weakTraceCount"] >= 1
    assert body["sourceLocatorMissingRisk"]["key"] == "SOURCE_LOCATOR_MISSING"
    assert body["sourceLocatorMissingRisk"]["reviewStatus"] == "open"
    assert body["evidenceTraceWeakRisk"]["key"] == "EVIDENCE_TRACE_WEAK"
    assert body["evidenceTraceWeakRisk"]["reviewStatus"] == "open"

    risks = list_deal_state_records(project_id, record_type="risk_issue")
    risk_by_key = {record["key"]: record for record in risks}
    assert risk_by_key["SOURCE_LOCATOR_MISSING"]["sourceLocator"]["artifactKey"] == body["auditId"]
    assert risk_by_key["EVIDENCE_TRACE_WEAK"]["value"]["weakTraceCount"] >= 1

    client.delete(f"/projects/{project_id}")


def test_feasibility_workbook_approve_persists_canonical_v2(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "approval.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    _isolated_upload_archive(tmp_path, monkeypatch)
    client = TestClient(app)
    created = client.post("/projects/", json={"project": {"name": "Import Approval Project"}}).json()
    project_id = created["project"]["id"]
    draft = {
        "version": "realytics-feasibility-study-v2",
        "project_id": project_id,
        "project_name": "Import Approval Project",
        "asset_type": "mixed_use",
        "program": {
            "gross_floor_area_sqm": {
                "key": "gross_floor_area_sqm",
                "label": "Gross floor area",
                "value": 45_000,
                "unit": "SQM",
            }
        },
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
        "raw_import": {
            "mappedCells": {
                "grossFloorArea": "Inputs!D15",
                "totalRevenue": "Business Summary!I24",
                "totalCost": "Business Summary!I81",
                "pfAmount": "Cash Flow!D34",
            },
            "governedFactBasis": {
                "revenue.total_sales_revenue": {
                    "vatTreatment": "included",
                    "amountScope": "gross",
                    "allocationBasis": "direct",
                    "currency": "KRW",
                },
                "cost.total_project_cost": {
                    "vatTreatment": "included",
                    "amountScope": "gross",
                    "allocationBasis": "mixed",
                    "allocationMethod": "approved_workbook_total",
                    "currency": "KRW",
                },
                "financing.pf_amount": {
                    "vatTreatment": "not_applicable",
                    "amountScope": "not_applicable",
                    "allocationBasis": "not_applicable",
                    "financingBalanceBasis": "commitment",
                    "currency": "KRW",
                },
            },
        },
    }
    source, validation = _server_validated_workbook_source(project_id, draft)

    candidate = client.post(
        f"/projects/{project_id}/feasibility-workbook/import-candidate",
        json={
            "importId": "fs-import-test",
            "feasibilityStudyV2Draft": draft,
            "sourceDocument": source,
            "validation": validation,
        },
    )
    assert candidate.status_code == 200

    same_actor_approval = client.post(
        f"/projects/{project_id}/feasibility-workbook/approve",
        json={
            "importId": "fs-import-test",
            "approvedBy": "anonymous",
            "feasibilityStudyV2Draft": draft,
            "sourceDocument": source,
            "validation": validation,
        },
    )
    assert same_actor_approval.status_code == 400
    assert "maker-checker" in str(same_actor_approval.json()["detail"])

    approved = client.post(
        f"/projects/{project_id}/feasibility-workbook/approve",
        json={
            "importId": "fs-import-test",
            "approvedBy": "credit-officer",
            "comment": "Use signed workbook as canonical feasibility.",
            "feasibilityStudyV2Draft": draft,
            "sourceDocument": source,
            "validation": validation,
        },
    )

    assert approved.status_code == 200
    body = approved.json()
    assert body["canonicalFeasibilityStudyV2"]["raw_import"]["canonicalStatus"] == "approved"
    assert body["approvedInputSnapshot"]["approvalStatus"] == "approved"
    assert body["parserV2Bridge"]["approvedFields"] == [
        "program.gross_floor_area_sqm",
        "revenue.total_sales_revenue",
        "cost.total_project_cost",
        "financing.pf_amount",
    ]
    assert (
        body["canonicalFeasibilityStudyV2"]["input_references"][
            "approved_input_snapshot_id"
        ]
        == body["approvedInputSnapshot"]["snapshotId"]
    )
    assert body["feasibilityInput"]["total_revenue"] == 100000000000
    fetched = client.get(f"/projects/{project_id}").json()
    assert fetched["feasibilityStudyV2"]["project_name"] == "Import Approval Project"
    assert fetched["_meta"]["latestFeasibilityWorkbookImportId"] == "fs-import-test"

    client.delete(f"/projects/{project_id}")


def test_authenticated_workbook_approval_rejects_spoofed_reviewer(
    monkeypatch,
):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    client = TestClient(app)
    project_id = client.post(
        "/projects/",
        json={"project": {"name": "Reviewer Identity Project"}},
    ).json()["project"]["id"]
    verified_context = {
        "authenticated": True,
        "user": {
            "userId": "verified-reviewer",
            "tenantId": "tenant-a",
            "status": "active",
        },
        "session": None,
        "role": "reviewer",
        "permissions": ["assumption:override"],
        "authorization": {
            "allowed": True,
            "permission": "assumption:override",
        },
    }
    monkeypatch.setattr(
        projects,
        "authorize_request",
        lambda _request, _permission: verified_context,
    )
    monkeypatch.setattr(
        projects,
        "identity_context_from_request",
        lambda _request: verified_context,
    )

    response = client.post(
        f"/projects/{project_id}/feasibility-workbook/approve",
        json={
            "approvedBy": "impersonated-reviewer",
            "feasibilityStudyV2Draft": {},
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "approval_actor_mismatch"
    client.delete(f"/projects/{project_id}")


def test_feasibility_workbook_approval_rejects_metadata_without_archive_object(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "approval-metadata-only.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    _isolated_upload_archive(tmp_path, monkeypatch)
    client = TestClient(app)
    project_id = client.post(
        "/projects/",
        json={"project": {"name": "Metadata Only Workbook"}},
    ).json()["project"]["id"]
    draft = {
        "version": "realytics-feasibility-study-v2",
        "project_id": project_id,
        "project_name": "Metadata Only Workbook",
        "asset_type": "mixed_use",
        "metrics": {"total_revenue": 100_000_000_000, "total_cost": 85_000_000_000},
    }
    forged_hash = "f" * 64
    source, validation = _validated_workbook_source_record(
        draft,
        {
            "archiveId": forged_hash,
            "archiveObjectKey": f"{project_id}/{forged_hash}.xlsx",
            "archiveStored": True,
        },
    )
    projects.source_document_repository().save_upload_metadata(project_id, source)

    response = client.post(
        f"/projects/{project_id}/feasibility-workbook/approve",
        json={
            "feasibilityStudyV2Draft": draft,
            "sourceDocument": source,
            "validation": validation,
        },
    )

    assert response.status_code == 400
    assert "not archived for this project" in response.json()["detail"]
    assert not client.get(f"/projects/{project_id}").json().get("canonicalFeasibilityStudyV2")
    client.delete(f"/projects/{project_id}")


def test_feasibility_workbook_approval_rejects_cross_project_archive_object(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "approval-cross-archive.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    archive_repository = _isolated_upload_archive(tmp_path, monkeypatch)
    client = TestClient(app)
    source_project_id = client.post(
        "/projects/",
        json={"project": {"name": "Archive Owner Project"}},
    ).json()["project"]["id"]
    route_project_id = client.post(
        "/projects/",
        json={"project": {"name": "Archive Claim Project"}},
    ).json()["project"]["id"]
    draft = {
        "version": "realytics-feasibility-study-v2",
        "project_id": route_project_id,
        "project_name": "Archive Claim Project",
        "asset_type": "mixed_use",
        "metrics": {"total_revenue": 100_000_000_000, "total_cost": 85_000_000_000},
    }
    archive_meta = archive_repository.archive_upload(
        source_project_id,
        b"dummy workbook bytes owned by another project",
        "cross-project.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    source, validation = _validated_workbook_source_record(draft, archive_meta)
    projects.source_document_repository().save_upload_metadata(route_project_id, source)

    response = client.post(
        f"/projects/{route_project_id}/feasibility-workbook/approve",
        json={
            "feasibilityStudyV2Draft": draft,
            "sourceDocument": source,
            "validation": validation,
        },
    )

    assert response.status_code == 400
    assert "not archived for this project" in response.json()["detail"]
    assert not client.get(f"/projects/{route_project_id}").json().get("canonicalFeasibilityStudyV2")
    client.delete(f"/projects/{source_project_id}")
    client.delete(f"/projects/{route_project_id}")


def test_feasibility_workbook_approval_rejects_tampered_archive_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "approval-archive-tamper.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    archive_repository = _isolated_upload_archive(tmp_path, monkeypatch)
    client = TestClient(app)
    project_id = client.post(
        "/projects/",
        json={"project": {"name": "Tampered Archive Workbook"}},
    ).json()["project"]["id"]
    draft = {
        "version": "realytics-feasibility-study-v2",
        "project_id": project_id,
        "project_name": "Tampered Archive Workbook",
        "asset_type": "mixed_use",
        "metrics": {"total_revenue": 100_000_000_000, "total_cost": 85_000_000_000},
    }
    source, validation = _server_validated_workbook_source(project_id, draft)
    resolved = archive_repository.resolve_download_metadata(project_id, source)
    assert resolved is not None and resolved["path"] is not None
    resolved["path"].write_bytes(b"tampered after validation")

    response = client.post(
        f"/projects/{project_id}/feasibility-workbook/approve",
        json={
            "feasibilityStudyV2Draft": draft,
            "sourceDocument": source,
            "validation": validation,
        },
    )

    assert response.status_code == 400
    assert "not archived for this project" in response.json()["detail"]
    assert not client.get(f"/projects/{project_id}").json().get("canonicalFeasibilityStudyV2")
    client.delete(f"/projects/{project_id}")


def test_feasibility_workbook_approval_rejects_tampered_server_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "approval-tamper.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    _isolated_upload_archive(tmp_path, monkeypatch)
    client = TestClient(app)
    project_id = client.post("/projects/", json={"project": {"name": "Draft Hash Project"}}).json()["project"]["id"]
    draft = {
        "version": "realytics-feasibility-study-v2",
        "project_id": project_id,
        "project_name": "Draft Hash Project",
        "asset_type": "mixed_use",
        "metrics": {"total_revenue": 100_000_000_000, "total_cost": 85_000_000_000},
    }
    source, validation = _server_validated_workbook_source(project_id, draft)
    tampered = {**draft, "metrics": {**draft["metrics"], "total_revenue": 120_000_000_000}}

    response = client.post(
        f"/projects/{project_id}/feasibility-workbook/approve",
        json={
            "feasibilityStudyV2Draft": tampered,
            "sourceDocument": source,
            "validation": validation,
        },
    )

    assert response.status_code == 400
    assert "differs from the server-extracted source draft" in response.json()["detail"]
    assert not client.get(f"/projects/{project_id}").json().get("canonicalFeasibilityStudyV2")
    client.delete(f"/projects/{project_id}")


def test_feasibility_workbook_approval_rejects_cross_project_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "approval-cross-project.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    _isolated_upload_archive(tmp_path, monkeypatch)
    client = TestClient(app)
    source_project_id = client.post("/projects/", json={"project": {"name": "Source Project"}}).json()["project"]["id"]
    route_project_id = client.post("/projects/", json={"project": {"name": "Route Project"}}).json()["project"]["id"]
    draft = {
        "version": "realytics-feasibility-study-v2",
        "project_id": source_project_id,
        "project_name": "Source Project",
        "asset_type": "mixed_use",
        "metrics": {"total_revenue": 100_000_000_000, "total_cost": 85_000_000_000},
    }
    source, validation = _server_validated_workbook_source(route_project_id, draft)

    response = client.post(
        f"/projects/{route_project_id}/feasibility-workbook/approve",
        json={
            "feasibilityStudyV2Draft": draft,
            "sourceDocument": source,
            "validation": validation,
        },
    )

    assert response.status_code == 400
    assert "project_id does not match" in response.json()["detail"]
    assert not client.get(f"/projects/{route_project_id}").json().get("canonicalFeasibilityStudyV2")
    client.delete(f"/projects/{source_project_id}")
    client.delete(f"/projects/{route_project_id}")


def test_feasibility_workbook_candidate_isolated_from_canonical_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "candidate.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    client = TestClient(app)
    created = client.post("/projects/", json={
        "project": {"name": "Workbook Candidate Project"},
        "feasibility_input": {"total_revenue": 123},
    }).json()
    project_id = created["project"]["id"]
    draft = {
        "version": "realytics-feasibility-study-v2",
        "project_id": project_id,
        "project_name": "Workbook Candidate Project",
        "asset_type": "mixed_use",
        "product_mix": [{"key": "apartment", "asset_type": "apartment", "product_name": "아파트", "unit_count": 120}],
        "schedule": [{"key": "completion", "label": "준공", "planned_date": "2028-01-01"}],
        "metrics": {"total_revenue": 100000000000, "total_cost": 85000000000},
        "financing": [{"tranche_id": "tr_a", "name": "Tr.A", "priority": "senior", "amount": 60000000000}],
    }

    response = client.post(
        f"/projects/{project_id}/feasibility-workbook/import-candidate",
        json={
            "feasibilityStudyV2Draft": draft,
            "validation": {"status": "valid"},
            "sourceDocument": {"originalFileName": "candidate.xlsx"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "candidate"
    assert body["authoritative"] is False
    assert body["feasibilityInputPreview"]["total_revenue"] == 100000000000
    fetched = client.get(f"/projects/{project_id}").json()
    assert fetched["_meta"]["feasibilityWorkbookCandidateStatus"] == "candidate"
    assert fetched["feasibility_input"]["total_revenue"] == 123
    assert not fetched.get("canonicalFeasibilityStudyV2")
    assert not fetched.get("feasibilityStudyV2")
    assert not fetched.get("product_mix")
    assert not fetched.get("pf_structure")

    client.delete(f"/projects/{project_id}")


def test_feasibility_workbook_approval_rejects_blocking_formula_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "approval-blocked.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    _isolated_upload_archive(tmp_path, monkeypatch)
    client = TestClient(app)
    project_id = client.post("/projects/", json={"project": {"name": "Blocked Workbook"}}).json()["project"]["id"]
    draft = {
        "version": "realytics-feasibility-study-v2",
        "project_id": project_id,
        "project_name": "Blocked Workbook",
        "raw_import": {"validationStatus": "warning"},
    }
    source, validation = _server_validated_workbook_source(
        project_id,
        draft,
        status="warning",
        blocking_formula_errors=True,
    )

    response = client.post(
        f"/projects/{project_id}/feasibility-workbook/approve",
        json={
            "feasibilityStudyV2Draft": draft,
            "sourceDocument": source,
            "validation": validation,
        },
    )

    assert response.status_code == 400
    assert "not approval-ready" in response.json()["detail"]
    fetched = client.get(f"/projects/{project_id}").json()
    assert not fetched.get("canonicalFeasibilityStudyV2")
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
