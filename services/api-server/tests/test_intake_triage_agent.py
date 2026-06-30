from agents.ai_connection import AIExtractionResult
from agents.intake_triage_agent import triage_intake_documents
from fastapi.testclient import TestClient
from main import app


def test_triage_recommends_pf_for_feasibility_and_risk_docs():
    result = triage_intake_documents(
        [
            {"filename": "본리동 사업수지 PF검토.xlsx", "documentType": "feasibility_workbook", "label": "사업수지", "confidence": 0.9},
            {"filename": "PF 리스크 보고서.xlsx", "documentType": "risk_report_workbook", "label": "리스크", "confidence": 0.8},
        ],
        ai_context={"provider": "claude_desktop"},
    )

    assert result["recommendedTrack"] == "pf"
    assert result["recommendationSource"] == "deterministic"
    assert result["tracks"][0]["track"] == "pf"
    assert result["tracks"][0]["recommended"] is True


def test_triage_recommends_real_asset_finance_for_rent_roll_and_appraisal():
    result = triage_intake_documents(
        [
            {"filename": "rent_roll_2026.xlsx", "documentType": "market_evidence", "label": "임대내역", "confidence": 0.85},
            {"filename": "감정평가서.pdf", "documentType": "land_evidence", "label": "감정평가", "confidence": 0.75},
        ],
        ai_context={"provider": "claude_desktop"},
    )

    assert result["recommendedTrack"] == "real_asset_finance"
    assert any(track["track"] == "real_asset_finance" and track["score"] > 0 for track in result["tracks"])


def test_triage_can_use_llm_recommendation_when_available():
    class FakeAI:
        def extract_json(self, prompt, *, system, ai_context):
            return AIExtractionResult(
                data={
                    "recommended": "finance",
                    "rationale": {
                        "finance": "담보와 대출 관련 문서 신호가 있어 금융 트랙을 추천합니다.",
                        "pf": "PF 관련성은 있으나 금융구조 검토가 우선입니다.",
                    },
                },
                meta={"provider": "gemini", "model": "fake"},
            )

    result = triage_intake_documents(
        [{"filename": "담보대출 리파이낸싱.xlsx", "documentType": "risk_report_workbook", "label": "리스크", "confidence": 0.7}],
        ai_context={"provider": "gemini", "runtime_provider": "openai_compatible", "model": "fake", "supports_json_mode": True},
        ai=FakeAI(),
    )

    assert result["recommendedTrack"] == "finance"
    assert result["recommendationSource"] == "llm"
    assert result["aiAssisted"] is True


def test_intake_triage_endpoint_returns_track_cards():
    client = TestClient(app)
    created = client.post("/projects/", json={"project": {"name": "Triage Endpoint Test"}})
    assert created.status_code == 200
    project_id = created.json()["project"]["id"]

    res = client.post(
        f"/projects/{project_id}/intake/triage",
        json={
            "files": [
                {
                    "filename": "사업계획서.pdf",
                    "documentType": "business_plan",
                    "label": "사업계획서 / IR",
                    "confidence": 0.85,
                }
            ]
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["schemaVersion"] == "realytics-intake-triage-v1"
    assert body["persisted"] is True
    assert "confidenceBasis" in body
    assert body["recommendedTrack"] in {"development", "pf", "finance", "real_asset_finance", "reit", "fund"}
    assert len(body["tracks"]) == 6

    detail = client.get(f"/projects/{project_id}")
    assert detail.status_code == 200
    project = detail.json()
    assert project["latestIntakeTriage"]["recommendedTrack"] == body["recommendedTrack"]
    assert len(project["intakeTriageHistory"]) == 1
