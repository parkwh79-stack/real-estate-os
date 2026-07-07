from fastapi.testclient import TestClient
from main import app
from routers.projects import PROJECTS_DB
from storage import upsert_project_record


def first_project_id(client: TestClient) -> str:
    return client.get("/projects/").json()[0]["project"]["id"]


def test_chief_orchestration_endpoints():
    client = TestClient(app)
    project_id = first_project_id(client)

    # Unblock land_legal by updating the project record in the db
    project_data = PROJECTS_DB[project_id]
    project_data["source_documents"] = [{"documentType": "business_plan", "originalFileName": "business.pdf", "archiveId": "a1"}]
    project_data["land"] = {"pnu": "1111010100100010000", "officialLandPricePerSqm": 1000000}
    project_data["landEvidence"] = [{"evidenceType": "official_land_price", "pnu": "1111010100100010000"}]
    upsert_project_record(project_id, project_data)

    # 1. Get Plan
    res = client.post(
        f"/projects/{project_id}/chief-orchestration/plan",
        json={"analysisPurpose": "unit_test_plan", "workflowTrack": "pf"}
    )
    assert res.status_code == 200
    plan = res.json()
    assert plan["schemaVersion"] == "realytics-chief-deal-orchestration-v1"
    assert len(plan["agentPods"]) == 6
    assert plan["analysisPurpose"] == "unit_test_plan"
    assert plan["taskGraph"]["workflowTrack"] == "pf"
    assert ("market_product", "pf_credit") in {
        (edge["from"], edge["to"]) for edge in plan["taskGraph"]["edges"]
    }

    # 2. Run Pod (e.g. land_legal with trigger)
    res_run = client.post(
        f"/projects/{project_id}/chief-orchestration/run-pod",
        json={
            "podKey": "land_legal",
            "workflowTrack": "pf",
            "options": {"forceTrigger": "legal_product_infeasible", "podAiReviewMode": "deterministic"}
        }
    )
    assert res_run.status_code == 200
    plan_updated = res_run.json()
    
    runs = {run["podKey"]: run for run in plan_updated["podRuns"]}
    assert runs["land_legal"]["status"] == "completed"
    assert runs["land_legal"]["artifactKeys"]
    assert runs["market_product"]["status"] == "needs_recalculation"
    assert runs["feasibility_cashflow"]["status"] == "needs_recalculation"
    assert runs["pf_credit"]["status"] == "needs_recalculation"

    artifacts_res = client.get(f"/projects/{project_id}/chief-orchestration/artifacts?pod_key=land_legal")
    assert artifacts_res.status_code == 200
    artifacts = artifacts_res.json()["records"]
    assert artifacts
    assert artifacts[0]["podKey"] == "land_legal"
    assert artifacts[0]["result"]["artifactType"] == "land_legal_pod_result"
    assert "legalPermitReview" in artifacts[0]["result"]
    assert artifacts[0]["podAiReviewId"]

    reviews_res = client.get(f"/projects/{project_id}/chief-orchestration/pod-ai-reviews?pod_key=land_legal")
    assert reviews_res.status_code == 200
    reviews = reviews_res.json()["records"]
    assert reviews
    assert reviews[0]["podKey"] == "land_legal"
    assert reviews[0]["sourceArtifactKey"] == artifacts[0]["artifactKey"]

    review_res = client.get(f"/projects/{project_id}/chief-orchestration/pod-ai-reviews/{reviews[0]['reviewId']}")
    assert review_res.status_code == 200
    assert review_res.json()["agentId"] == "LandLegalPodAIReviewer"

    events_res = client.get(f"/projects/{project_id}/chief-orchestration/recalculation-events?source_pod=land_legal")
    assert events_res.status_code == 200
    events = events_res.json()["records"]
    assert events
    assert events[0]["trigger"] == "legal_product_infeasible"
    assert "pf_credit" in events[0]["targetPods"]

    artifact_key = artifacts[0]["artifactKey"]
    artifact_res = client.get(f"/projects/{project_id}/chief-orchestration/artifacts/{artifact_key}")
    assert artifact_res.status_code == 200
    assert artifact_res.json()["artifactKey"] == artifact_key
    
    # 3. Check that the recalculation event is persisted (we can fetch plan again)
    res_fetch = client.post(
        f"/projects/{project_id}/chief-orchestration/plan",
        json={"analysisPurpose": "unit_test_plan", "workflowTrack": "pf"}
    )
    assert res_fetch.status_code == 200
    plan_fetched = res_fetch.json()
    runs_fetched = {run["podKey"]: run for run in plan_fetched["podRuns"]}
    assert runs_fetched["land_legal"]["status"] == "completed"
    assert runs_fetched["market_product"]["status"] == "needs_recalculation"

    res_all = client.post(
        f"/projects/{project_id}/chief-orchestration/run-all-pods",
        json={"workflowTrack": "pf", "options": {"podAiReviewMode": "deterministic"}},
    )
    assert res_all.status_code == 200
    all_body = res_all.json()
    assert all_body["runAllSummary"]["requestedPodCount"] == 6
    assert all_body["runAllSummary"]["completedCount"] >= 1
    assert "rows" in all_body["runAllSummary"]


def test_workspace_context_persists_shared_deal_state_and_page_data():
    client = TestClient(app)
    project_id = first_project_id(client)
    project_data = PROJECTS_DB[project_id]
    project_data["source_documents"] = [
        {"documentType": "business_plan", "originalFileName": "business.pdf", "archiveId": "ctx-a1"}
    ]
    project_data["landLedger"] = {
        "parcels": [
            {"sequence": 1, "addressFragment": "대구 달서구 본동 123-4", "normalizedLotNumber": "123-4"}
        ]
    }
    upsert_project_record(project_id, project_data)

    res = client.get(f"/projects/{project_id}/workspace/context?page=input_package&track=pf")
    assert res.status_code == 200
    body = res.json()
    assert body["schemaVersion"] == "realytics-workspace-page-context-v1"
    assert body["projectMemory"]["projectId"] == project_id
    assert body["pageData"]["sourceDocumentSummary"]["count"] >= 1
    assert body["pageData"]["landLedgerSummary"]["parcelCount"] >= 1
    assert body["pageDataContract"]["page"] == "input_package"
    assert set(body["pageDataContract"]["requiredPageDataFields"]).issubset(body["pageData"].keys())
    assert body["dealStateGraph"]["schemaVersion"] == "realytics-shared-deal-state-graph-record-v1"
    assert PROJECTS_DB[project_id]["chief_orchestration_state"]["sharedDealStateGraph"]["projectId"] == project_id


def test_workspace_context_page_data_contracts_for_core_pages():
    client = TestClient(app)
    project_id = first_project_id(client)
    project_data = PROJECTS_DB[project_id]
    project_data.update(
        {
            "source_documents": [
                {"documentType": "business_plan", "originalFileName": "business.pdf", "archiveId": "ctx-contract-a1"}
            ],
            "land": {"address": "서울특별시 중구 세종대로 110", "pnu": "1114010100100010000"},
            "landLedger": {
                "parcels": [
                    {
                        "sequence": 1,
                        "addressFragment": "서울특별시 중구 세종대로 110",
                        "normalizedLotNumber": "110",
                        "pnu": "1114010100100010000",
                    }
                ]
            },
            "market": {"transactionComparables": []},
            "marketability": {"score": 72},
            "feasibility": {"totalSalesRevenue": 100000000000},
            "cashFlow": {"summary": {"maxMinus": -10000000000}},
            "pfStructure": {"tranches": [{"tranche": "Tr.A", "amount": 50000000000}]},
            "riskRegister": {"risks": [{"code": "SENIOR_SHORTFALL", "severity": "critical"}]},
            "creditReviewDecision": {"decision": "hold"},
        }
    )
    upsert_project_record(project_id, project_data)

    pages = [
        "overview",
        "input_package",
        "land",
        "map",
        "marketability",
        "feasibility",
        "cashflow",
        "pf",
        "risk",
        "evidence",
    ]
    for page in pages:
        res = client.get(f"/projects/{project_id}/workspace/context?page={page}&track=pf")
        assert res.status_code == 200
        body = res.json()
        contract = body["pageDataContract"]
        assert contract["contractVersion"] == "realytics-workspace-page-data-contract-v1"
        assert body["normalizedPage"] == contract["page"]
        missing = set(contract["requiredPageDataFields"]) - set(body["pageData"].keys())
        assert not missing, f"{page} missing pageData fields: {missing}"
        assert contract["sourceOfTruth"], f"{page} must document DB/repository source of truth"
