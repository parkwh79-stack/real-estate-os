from fastapi.testclient import TestClient

from main import app


def test_glossary_seed_resolves_korean_alias(monkeypatch, tmp_path):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "glossary.sqlite3"))
    client = TestClient(app)

    terms = client.get("/glossary/terms?q=총분양수입")

    assert terms.status_code == 200
    body = terms.json()
    assert body["count"] >= 1
    assert body["terms"][0]["canonicalField"] == "feasibility_input.total_revenue"

    resolved = client.get("/glossary/resolve", params={"term": "분양수입"})

    assert resolved.status_code == 200
    assert resolved.json()["matched"] is True
    assert resolved.json()["term"]["termId"] == "total_sales_revenue"


def test_glossary_upsert_and_canonical_field_map(monkeypatch, tmp_path):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "glossary.sqlite3"))
    client = TestClient(app)

    created = client.post(
        "/glossary/terms",
        headers={"x-realytics-user-id": "qa-reviewer"},
        json={
            "termId": "dsra",
            "preferredTerm": "DSRA",
            "displayNameKo": "원리금상환준비계좌",
            "aliases": ["Debt Service Reserve Account", "상환준비금"],
            "definitionKo": "원리금 상환 부족 위험을 완충하기 위해 적립하는 준비계좌입니다.",
            "canonicalField": "pf_structure.dsra.amount",
            "canonicalPath": "pfStructure.dsra.amount",
            "category": "pf_structure",
            "dataType": "money",
            "unit": "KRW",
            "reviewStatus": "approved",
        },
    )

    assert created.status_code == 200
    term = created.json()["term"]
    assert term["termId"] == "dsra"
    assert term["reviewHistory"][-1]["by"] == "qa-reviewer"

    field_map = client.get("/glossary/canonical-field-map")

    assert field_map.status_code == 200
    assert "pf_structure.dsra.amount" in field_map.json()["fields"]
    assert field_map.json()["termCount"] >= 32
    assert field_map.json()["categoryCounts"]["pf_structure"] >= 4


def test_glossary_seed_resolves_finance_legal_and_real_asset_terms(monkeypatch, tmp_path):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "glossary.sqlite3"))
    client = TestClient(app)

    cases = [
        ("Tr.A", "senior_tranche", "pf_structure.tranches.senior.amount"),
        ("책임준공확약", "responsible_completion", "credit_enhancement.responsible_completion"),
        ("관리형토지신탁", "management_land_trust", "legal.trust.managementLandTrust"),
        ("기한이익상실", "eod", "legal.eodTriggers"),
        ("Cap Rate", "cap_rate", "real_asset.capRate"),
        ("선매입", "forward_sale", "real_asset.forwardSale.price"),
    ]

    for query, term_id, canonical_field in cases:
        resolved = client.get("/glossary/resolve", params={"term": query})
        assert resolved.status_code == 200
        body = resolved.json()
        assert body["matched"] is True
        assert body["term"]["termId"] == term_id
        assert body["term"]["canonicalField"] == canonical_field


def test_glossary_rejects_approved_term_without_canonical_field(monkeypatch, tmp_path):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "glossary.sqlite3"))
    client = TestClient(app)

    response = client.post(
        "/glossary/terms",
        json={
            "termId": "unmapped_pf_term",
            "preferredTerm": "Unmapped PF Term",
            "displayNameKo": "미매핑 PF 용어",
            "reviewStatus": "approved",
        },
    )

    assert response.status_code == 400
    assert "canonicalField is required" in response.json()["detail"]
