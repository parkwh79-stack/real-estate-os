from fastapi.testclient import TestClient

from agents.orchestration_agent import OrchestrationAgent
from main import app


def test_land_evidence_upsert_and_list_api():
    client = TestClient(app)
    project_id = client.get("/projects/").json()[0]["project"]["id"]

    saved = client.post(
        f"/projects/{project_id}/land-evidence",
        headers={"x-realytics-user-id": "land-analyst"},
        json={
            "records": [
                {
                    "evidenceType": "official_land_price",
                    "pnu": "2729011700101230004",
                    "pricePerSqm": 1500000,
                    "baseYear": 2026,
                    "baseDate": "2026-01-01",
                    "sourceName": "manual pilot evidence",
                    "sourceUrlOrEvidenceId": "evidence-official-price-001",
                },
                {
                    "evidenceType": "registry_record",
                    "pnu": "2729011700101230004",
                    "registeredOwner": "Seller A",
                    "ownerMatchStatus": "matched",
                    "mortgageExists": False,
                    "verificationStatus": "attached_pending_legal_review",
                    "sourceUrlOrEvidenceId": "registry-pdf-001",
                },
            ]
        },
    )

    assert saved.status_code == 200
    assert saved.json()["count"] == 2

    listed = client.get(
        f"/projects/{project_id}/land-evidence",
        headers={"x-realytics-user-id": "land-analyst"},
        params={"pnu": "2729011700101230004"},
    )

    assert listed.status_code == 200
    records = listed.json()["records"]
    assert {record["evidenceType"] for record in records} >= {"official_land_price", "registry_record"}


def test_land_ledger_enrich_can_persist_land_evidence_records():
    client = TestClient(app)
    project_id = client.get("/projects/").json()[0]["project"]["id"]

    enriched = client.post(
        f"/projects/{project_id}/land-ledger/enrich",
        headers={"x-realytics-user-id": "land-analyst"},
        json={
            "persistEvidence": True,
            "landLedger": {
                "parcels": [
                    {
                        "sequence": 1,
                        "addressFragment": "Bonri-dong 123-4",
                        "normalizedLotNumber": "123-4",
                        "mainLotNo": 123,
                        "subLotNo": 4,
                        "areaSqm": 100,
                        "areaPy": 30.25,
                        "owners": [
                            {
                                "ownerName": "Seller A",
                                "share": 1.0,
                                "individualContractAmount": 100000000,
                                "depositPaid": 10000000,
                                "depositUnpaid": 0,
                                "interimPayment": 0,
                                "balancePayment": 90000000,
                            }
                        ],
                    }
                ]
            },
            "options": {
                "legalDongCode": "2729011700",
                "officialLandPrices": [
                    {
                        "pnu": "2729011700101230004",
                        "pricePerSqm": 1500000,
                        "baseYear": 2026,
                        "baseDate": "2026-01-01",
                        "sourceName": "manual pilot evidence",
                    }
                ],
                "registryEvidence": [
                    {
                        "pnu": "2729011700101230004",
                        "evidenceId": "registry-pdf-001",
                        "registeredOwner": "Seller A",
                    }
                ],
            },
        },
    )

    assert enriched.status_code == 200
    assert enriched.json()["persistedLandEvidenceCount"] >= 3

    listed = client.get(
        f"/projects/{project_id}/land-evidence",
        headers={"x-realytics-user-id": "land-analyst"},
        params={"pnu": "2729011700101230004"},
    )
    assert listed.status_code == 200
    assert any(record["evidenceType"] == "official_land_price" for record in listed.json()["records"])


def test_land_acquisition_status_record_clears_acquisition_gate():
    client = TestClient(app)
    project_id = client.get("/projects/").json()[0]["project"]["id"]

    saved = client.post(
        f"/projects/{project_id}/land-evidence",
        headers={"x-realytics-user-id": "land-analyst"},
        json={
            "records": [
                {
                    "evidenceType": "pnu_resolution",
                    "pnu": "2729011700101230004",
                    "parcelKey": "123-4",
                    "matchConfidence": 0.95,
                    "sourceName": "manual pnu evidence",
                    "verificationStatus": "resolved",
                },
                {
                    "evidenceType": "official_land_price",
                    "pnu": "2729011700101230004",
                    "parcelKey": "123-4",
                    "pricePerSqm": 1500000,
                    "baseYear": 2026,
                    "baseDate": "2026-01-01",
                    "sourceName": "manual official price evidence",
                    "sourceUrlOrEvidenceId": "official-price-evidence-001",
                },
                {
                    "evidenceType": "registry_record",
                    "pnu": "2729011700101230004",
                    "parcelKey": "123-4",
                    "evidenceId": "registry-pdf-001",
                    "registeredOwner": "Seller A",
                    "mortgageExists": False,
                    "verificationStatus": "attached_pending_legal_review",
                },
                {
                    "evidenceType": "land_acquisition_status",
                    "pnu": "2729011700101230004",
                    "parcelKey": "123-4",
                    "acquisitionAreaRatio": 1.0,
                    "acquisitionStatus": "trust_registered",
                    "sourceName": "manual closing evidence",
                    "sourceUrlOrEvidenceId": "closing-confirmation-001",
                    "verificationStatus": "verified_for_pf_review",
                },
            ]
        },
    )
    assert saved.status_code == 200

    run = client.post(
        f"/projects/{project_id}/orchestrations/pf-review",
        json={"requestedBy": "test"},
    )
    assert run.status_code == 200
    land_review = run.json()["artifacts"]["landAgentReview"]
    assert land_review["evidenceReadiness"]["acquisitionReady"] is True
    assert land_review["summary"]["acquisitionAreaRatio"] == 1.0
    land_gate_by_code = {
        gate["code"]: gate
        for gate in run.json()["artifacts"]["approvalReadinessGate"]["gates"]
    }
    assert land_gate_by_code["LAND_ACQUISITION_READY"]["pass"] is True


def test_pf_review_run_consumes_persisted_land_evidence():
    client = TestClient(app)
    created = client.post(
        "/projects/",
        json={
            "project": {"name": "Land Evidence Orchestration Test", "type": "mixed_use"},
            "land": {"address": "Daegu Bonri-dong", "area_sqm": 100},
            "land_acquisition": [{"area_sqm": 100, "share": 1.0}],
            "feasibility_input": {"total_revenue": 200000000000, "total_cost": 150000000000},
            "pf_structure": [{"name": "Senior", "amount": 50000000000, "priority": 1}],
        },
    )
    assert created.status_code == 200
    project_id = created.json()["project"]["id"]

    saved = client.post(
        f"/projects/{project_id}/land-evidence",
        headers={"x-realytics-user-id": "land-analyst"},
        json={
            "records": [
                {
                    "evidenceType": "pnu_resolution",
                    "pnu": "2729011700101230004",
                    "matchConfidence": 0.95,
                    "sourceUrlOrEvidenceId": "pnu-resolver-001",
                },
                {
                    "evidenceType": "official_land_price",
                    "pnu": "2729011700101230004",
                    "pricePerSqm": 1500000,
                    "baseYear": 2026,
                    "baseDate": "2026-01-01",
                    "sourceUrlOrEvidenceId": "official-price-001",
                },
                {
                    "evidenceType": "registry_record",
                    "pnu": "2729011700101230004",
                    "registeredOwner": "Seller A",
                    "ownerMatchStatus": "matched",
                    "mortgageExists": False,
                    "sourceUrlOrEvidenceId": "registry-pdf-001",
                },
            ]
        },
    )
    assert saved.status_code == 200

    run = client.post(
        f"/projects/{project_id}/orchestrations/pf-review",
        headers={"x-realytics-user-id": "land-analyst"},
        json={},
    )

    assert run.status_code == 200
    land_review = run.json()["artifacts"]["landAgentReview"]
    legal_review = run.json()["artifacts"]["legalPermitReview"]
    assert land_review["evidenceReadiness"]["pnuReady"] is True
    assert land_review["evidenceReadiness"]["officialPriceReady"] is True
    assert land_review["evidenceReadiness"]["registryEvidenceReady"] is True
    assert "official-price-001" in land_review["evidenceIds"]
    land_rights = next(check for check in legal_review["checks"] if check["key"] == "land_rights")
    assert land_rights["severity"] == "clear"
    artifacts = run.json()["artifacts"]
    approval_gate = OrchestrationAgent()._approval_readiness_gate({
        "handoffs": [],
        "artifacts": {
            **artifacts,
            "qualityCheck": {"pass": True},
            "evidenceGraph": {"summary": {"riskRowsWithoutExplicitEvidenceCount": 0}},
        },
    })
    land_gate_by_code = {
        gate["code"]: gate
        for gate in approval_gate["gates"]
        if gate["code"].startswith("LAND_")
    }
    assert land_gate_by_code["LAND_PNU_READY"]["pass"] is True
    assert land_gate_by_code["LAND_OFFICIAL_PRICE_READY"]["pass"] is True
    assert land_gate_by_code["LAND_REGISTRY_EVIDENCE_READY"]["pass"] is True
    assert land_gate_by_code["LAND_ACQUISITION_READY"]["pass"] is True
    assert approval_gate["summary"]["landEvidenceReady"] is True
