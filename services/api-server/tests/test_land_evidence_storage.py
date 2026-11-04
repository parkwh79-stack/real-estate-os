import json
from pathlib import Path

from fastapi.testclient import TestClient

from agents.orchestration_agent import OrchestrationAgent
from main import app
from routers import projects
from storage import upsert_user


def _land_analyst_headers() -> dict[str, str]:
    upsert_user(
        {
            "userId": "land-analyst",
            "tenantId": "pytest-tenant",
            "role": "analyst",
            "status": "active",
        }
    )
    return {"x-realytics-user-id": "land-analyst"}


def _bondong_project_id(client: TestClient) -> str:
    """Find (or seed) the Daegu Bondong example project — hermetic against a
    drifted runtime DB (the first project is not guaranteed to be the seeded one
    with the parcel layout this acquisition-gate test depends on)."""
    for item in client.get("/projects/").json():
        if (item.get("project") or {}).get("name") == "Daegu Bondong Mixed-Use PF":
            return item["project"]["id"]
    example = json.loads(
        (Path(__file__).parents[1] / "data" / "examples" / "daegu_bondong_mixed_use_pf.json").read_text(encoding="utf-8")
    )
    created = client.post("/projects/", json=example).json()
    return created.get("id") or created["project"]["id"]


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
                "pnuResolutions": [{
                    "pnu": "2729011700101230004",
                    "sequence": 1,
                    "matchConfidence": 0.95,
                }],
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


def test_land_ledger_enrich_does_not_promote_representative_dong_to_parcel_pnu():
    client = TestClient(app)
    created = client.post("/projects/", json={"project": {"name": "Auto PNU ledger test"}})
    assert created.status_code == 200
    project_id = created.json()["project"]["id"]
    update = client.put(
        f"/projects/{project_id}",
        json={"land": {"pnu": "2729011700100010000"}},
    )
    assert update.status_code == 200

    enriched = client.post(
        f"/projects/{project_id}/land-ledger/enrich",
        headers={"x-realytics-user-id": "land-analyst"},
        json={
            "persist": True,
            "persistEvidence": True,
            "landLedger": {
                "parcels": [
                    {
                        "sequence": 1,
                        "addressFragment": "본리동 123-4",
                        "normalizedLotNumber": "123-4",
                        "mainLotNo": 123,
                        "subLotNo": 4,
                        "isMountain": False,
                        "areaSqm": 100,
                        "areaPy": 30.25,
                        "owners": [],
                    }
                ]
            },
            "options": {"mode": "unit_test_auto_project_pnu", "legalDongCode": "2729011700"},
        },
    )

    assert enriched.status_code == 200
    body = enriched.json()
    parcel = body["landLedger"]["parcels"][0]
    assert not parcel.get("pnu")
    assert parcel["pnuCandidate"] == "2729011700101230004"
    assert body["persistedLandEvidenceCount"] == 0

    listed = client.get(
        f"/projects/{project_id}/land-evidence",
        headers={"x-realytics-user-id": "land-analyst"},
        params={"pnu": "2729011700101230004"},
    )
    assert listed.status_code == 200
    assert listed.json()["records"] == []


def test_land_ledger_enrich_resolves_pnu_from_vworld_address_lookup(monkeypatch):
    client = TestClient(app)
    created = client.post("/projects/", json={
        "project": {"name": "VWorld PNU ledger test"},
        "land": {"address": "경기도용인시기흥구상갈동13번지일대"},
    })
    assert created.status_code == 200
    project_id = created.json()["project"]["id"]
    captured = []

    def _fake_resolve(address):
        captured.append(address)
        assert address == "경기도 용인시 기흥구 상갈동 13"
        return {
            "status": "resolved",
            "pnu": "4146311100100130000",
            "addressFragment": "경기도 용인시 기흥구 상갈동 13",
            "officialLandPricePerSqm": 1234000,
            "officialLandPriceBaseYear": 2026,
            "landCategory": "대",
            "areaSqm": 330.0,
        }

    monkeypatch.setattr(projects, "resolve_address_to_parcel", _fake_resolve)

    enriched = client.post(
        f"/projects/{project_id}/land-ledger/enrich",
        headers={"x-realytics-user-id": "land-analyst"},
        json={
            "persist": True,
            "persistEvidence": True,
            "landLedger": {
                "parcels": [
                    {
                        "sequence": 1,
                        "addressFragment": "13",
                        "normalizedLotNumber": "13",
                        "mainLotNo": 13,
                        "subLotNo": 0,
                        "isMountain": False,
                        "areaSqm": 330.0,
                        "owners": [],
                    }
                ]
            },
            "options": {"mode": "unit_test_vworld_address_pnu"},
        },
    )

    assert enriched.status_code == 200
    body = enriched.json()
    parcel = body["landLedger"]["parcels"][0]
    assert captured == ["경기도 용인시 기흥구 상갈동 13"]
    assert parcel["pnu"] == "4146311100100130000"
    assert parcel["officialLandPricePerSqm"] == 1234000
    assert body["persistedLandEvidenceCount"] >= 3


def test_land_acquisition_status_record_clears_acquisition_gate():
    client = TestClient(app)
    project_id = _bondong_project_id(client)

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
        headers=_land_analyst_headers(),
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
            "land_acquisition": [{"pnu": "2729011700101230004", "area_sqm": 100, "share": 1.0}],
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
        headers=_land_analyst_headers(),
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
