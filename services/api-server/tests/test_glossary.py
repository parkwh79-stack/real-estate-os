from fastapi.testclient import TestClient
import unicodedata

from main import app
from use_cases.glossary import capture_unmapped_term_surface_candidates_use_case


def test_glossary_seed_resolves_korean_alias(monkeypatch, tmp_path):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "glossary.sqlite3"))
    client = TestClient(app)

    terms = client.get("/glossary/terms", params={"q": "총분양수입"})

    assert terms.status_code == 200
    body = terms.json()
    assert body["count"] >= 1
    assert body["terms"][0]["canonicalField"] == "feasibility_input.total_revenue"

    resolved = client.get("/glossary/resolve", params={"term": "분양수입"})

    assert resolved.status_code == 200
    assert resolved.json()["matched"] is True
    assert resolved.json()["term"]["termId"] == "total_sales_revenue"


def test_glossary_resolution_normalizes_korean_unicode_surfaces(monkeypatch, tmp_path):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "glossary-normalized.sqlite3"))
    client = TestClient(app)

    nfd_surface = "\ufeff" + unicodedata.normalize("NFD", "총분양수입") + "\u200b"
    resolved = client.get("/glossary/resolve", params={"term": nfd_surface})

    assert resolved.status_code == 200
    assert resolved.json()["matched"] is True
    assert resolved.json()["term"]["canonicalField"] == "feasibility_input.total_revenue"


def test_glossary_resolution_repairs_common_utf8_mojibake(monkeypatch, tmp_path):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "glossary-mojibake.sqlite3"))
    client = TestClient(app)

    mojibake_surface = "총분양수입".encode("utf-8").decode("latin1")
    resolved = client.get("/glossary/resolve", params={"term": mojibake_surface})

    assert resolved.status_code == 200
    assert resolved.json()["matched"] is True
    assert resolved.json()["term"]["canonicalField"] == "feasibility_input.total_revenue"


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
        ("선매각", "forward_sale", "real_asset.forwardSale.price"),
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


def test_glossary_candidate_capture_and_approval(monkeypatch, tmp_path):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "glossary-candidates.sqlite3"))
    client = TestClient(app)

    captured = client.post(
        "/glossary/candidates/capture",
        headers={"x-realytics-user-id": "term-reviewer"},
        json={
            "projectId": "project-1",
            "runId": "run-1",
            "annotations": [
                {
                    "surface": "임대차확약",
                    "status": "unmapped_candidate",
                    "sourceField": "legal.conditions[0]",
                    "sourceLocator": {"documentId": "doc-1", "pageNumber": 7},
                    "confidence": 0.0,
                }
            ],
        },
    )

    assert captured.status_code == 200
    candidate = captured.json()["candidates"][0]
    assert candidate["status"] == "pending_review"
    assert candidate["projectId"] == "project-1"
    assert candidate["occurrenceCount"] == 1
    assert candidate["termSurface"]["rawText"] == "임대차확약"
    assert candidate["termSurface"]["normalizedSurface"] == "임대차확약"
    assert candidate["termSurface"]["sourceField"] == "legal.conditions[0]"
    assert "textQualityFlags" in candidate["termSurface"]

    listed = client.get("/glossary/candidates", params={"status": "pending_review", "project_id": "project-1"})
    assert listed.status_code == 200
    assert listed.json()["count"] == 1

    approved = client.post(
        f"/glossary/candidates/{candidate['candidateId']}/review",
        headers={"x-realytics-user-id": "term-reviewer"},
        json={
            "status": "approved",
            "termId": "lease_commitment",
            "preferredTerm": "임대차확약",
            "canonicalField": "real_asset.leaseCommitment",
            "category": "real_asset",
            "dataType": "boolean",
            "reason": "파일럿 법률 검토에서 반복 등장하는 임대차 선행조건",
        },
    )

    assert approved.status_code == 200
    body = approved.json()
    assert body["candidate"]["status"] == "approved"
    assert body["candidate"]["occurrenceCount"] == 1
    assert body["term"]["termId"] == "lease_commitment"
    assert body["term"]["canonicalField"] == "real_asset.leaseCommitment"

    resolved = client.get("/glossary/resolve", params={"term": "임대차확약"})
    assert resolved.status_code == 200
    assert resolved.json()["matched"] is True
    assert resolved.json()["term"]["termId"] == "lease_commitment"

    captured_again = client.post(
        "/glossary/candidates/capture",
        headers={"x-realytics-user-id": "term-reviewer"},
        json={
            "projectId": "project-1",
            "runId": "run-2",
            "annotations": [
                {
                    "surface": "임대차확약",
                    "status": "unmapped_candidate",
                    "sourceField": "legal.conditions[0]",
                    "sourceLocator": {"documentId": "doc-2", "pageNumber": 9},
                }
            ],
        },
    )
    assert captured_again.status_code == 200
    assert captured_again.json()["candidates"][0]["status"] == "approved"
    assert captured_again.json()["candidates"][0]["occurrenceCount"] == 2


def test_term_standard_v3_seed_import_and_resolution(monkeypatch, tmp_path):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "term-standard.sqlite3"))
    client = TestClient(app)

    seed = {
        "metadata": {"version": "v3.0", "name": "test seed"},
        "terms": [
            {
                "term_id": "TERM-TEST-0001",
                "canonical_ko": "Exit 분양률",
                "canonical_en": "Exit Sales Rate",
                "abbreviation": "",
                "category_l1": "분양/Exit",
                "category_l2": "분양·상환",
                "definition_ko": "PF 원리금 상환에 필요한 최소 누적 분양률입니다.",
                "standard_usage_rule": "Exit분양율, Exit분양률은 Exit 분양률로 정규화합니다.",
                "formula_or_logic": "required_repayment / total_sales_revenue",
                "synonyms": "Exit분양율|Exit분양률|exit sale rate",
                "data_type": "metric",
                "realytics_module": "PLATFORM9",
                "source_key": "PF_SAMPLE",
                "source_doc": "sample",
                "criticality": "high",
                "confidence": 0.98,
                "status": "approved",
                "version": "v3.0",
                "owner": "REALYTICS AI LAB",
                "created_at": "2026-06-30T00:00:00+09:00",
                "updated_at": "2026-06-30T00:00:00+09:00",
                "agent_module": "PLATFORM9_EXIT_AGENT",
                "report_section": "9. Sales / Exit / Unsold Analysis",
                "prompt_template_id": "PT_EXIT_REVIEW",
                "calculation_required": "Y",
                "data_source_required": "사업수지; PF 구조",
                "validation_rule": "0 <= value <= 1",
                "risk_score": 80,
                "risk_tier": "HIGH",
                "production_ready": "Y",
            }
        ],
        "synonym_mapping": {"Exit분양율": "TERM-TEST-0001"},
    }

    imported = client.post(
        "/glossary/standard-pack/import",
        headers={"x-realytics-user-id": "term-admin"},
        json={"seed": seed, "replaceExisting": True},
    )

    assert imported.status_code == 200
    assert imported.json()["importedCount"] == 1

    resolved = client.get("/glossary/resolve", params={"term": "Exit분양율"})

    assert resolved.status_code == 200
    body = resolved.json()
    assert body["matched"] is True
    assert body["term"]["termId"] == "TERM-TEST-0001"
    assert body["term"]["canonicalField"] == "scenario.exitSalesRate"
    assert body["term"]["agentModule"] == "PLATFORM9_EXIT_AGENT"
    assert body["term"]["riskTier"] == "HIGH"


def test_glossary_normalize_creates_candidate_for_unmapped_term(monkeypatch, tmp_path):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "term-normalize.sqlite3"))
    client = TestClient(app)

    response = client.post(
        "/glossary/normalize",
        headers={"x-realytics-user-id": "parser-agent"},
        json={
            "projectId": "project-term-1",
            "runId": "run-term-1",
            "items": [
                {
                    "surface": "분양 -10%/금리 +1% 동시충격",
                    "context": "PF Dashboard stress context",
                    "sourceField": "scenario.stress.memo",
                    "sourceLocator": {"documentId": "doc-1", "pageNumber": 4},
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["matchedCount"] == 0
    assert body["candidateCount"] == 1
    candidate = body["results"][0]["candidate"]
    assert candidate["status"] == "pending_review"
    assert candidate["projectId"] == "project-term-1"
    assert candidate["sourceLocator"]["pageNumber"] == 4


def test_unmapped_term_surfaces_are_captured_as_glossary_candidates(monkeypatch, tmp_path):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "term-surface-candidates.sqlite3"))

    result = capture_unmapped_term_surface_candidates_use_case(
        [
            {
                "rawText": "총분양수입",
                "normalizedSurface": "총분양수입",
                "termId": "total_sales_revenue",
                "canonicalField": "feasibility_input.total_revenue",
                "sourceField": "feasibility_input.total_revenue",
                "sourceLocator": {"pageNumber": 1, "sourceType": "document_page"},
            },
            {
                "rawText": "입주확약서 제출 조건",
                "normalizedSurface": "입주확약서 제출 조건",
                "termId": None,
                "canonicalField": None,
                "sourceField": "covenants.drawdown_conditions[0]",
                "sourceLocator": {"pageNumber": 9, "sourceType": "document_page"},
            },
        ],
        project_id="p-term-surface",
        run_id="run-term-surface",
        source="test",
    )

    assert result["surfaceCount"] == 2
    assert result["unmappedSurfaceCount"] == 1
    assert result["capturedCount"] == 1
    candidate = result["candidates"][0]
    assert candidate["surface"] == "입주확약서 제출 조건"
    assert candidate["projectId"] == "p-term-surface"
    assert candidate["runId"] == "run-term-surface"
    assert candidate["termSurface"]["rawText"] == "입주확약서 제출 조건"
    assert candidate["sourceLocator"]["pageNumber"] == 9


def test_glossary_candidate_merge_registers_alias_on_existing_term(monkeypatch, tmp_path):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "glossary-merge.sqlite3"))
    client = TestClient(app)

    created = client.post(
        "/glossary/terms",
        headers={"x-realytics-user-id": "term-reviewer"},
        json={
            "termId": "trust_beneficiary",
            "preferredTerm": "우선수익권",
            "canonicalField": "legal.security.trustBeneficiary",
            "category": "legal",
            "reviewStatus": "approved",
        },
    )
    assert created.status_code == 200

    captured = client.post(
        "/glossary/candidates/capture",
        headers={"x-realytics-user-id": "term-reviewer"},
        json={
            "projectId": "project-9",
            "runId": "run-9",
            "annotations": [
                {
                    "surface": "본건 신탁 우선수익 지정분",
                    "status": "unmapped_candidate",
                    "sourceField": "legal.conditions[1]",
                    "confidence": 0.0,
                }
            ],
        },
    )
    assert captured.status_code == 200
    candidate = captured.json()["candidates"][0]

    merged = client.post(
        f"/glossary/candidates/{candidate['candidateId']}/review",
        headers={"x-realytics-user-id": "term-reviewer"},
        json={"status": "merged", "termId": "trust_beneficiary"},
    )

    assert merged.status_code == 200
    body = merged.json()
    assert body["candidate"]["status"] == "merged"
    # The merge must LEARN: the surface becomes an alias on the target term...
    assert "본건 신탁 우선수익 지정분" in body["term"]["aliases"]
    assert body["term"]["termId"] == "trust_beneficiary"
    # ...so the next normalize/resolve maps it exactly instead of re-queueing.
    resolved = client.get("/glossary/resolve", params={"term": "본건 신탁 우선수익 지정분"})
    assert resolved.status_code == 200
    assert resolved.json()["matched"] is True
    assert resolved.json()["term"]["termId"] == "trust_beneficiary"


def test_glossary_candidate_merge_requires_target_term(monkeypatch, tmp_path):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "glossary-merge-missing.sqlite3"))
    client = TestClient(app)

    captured = client.post(
        "/glossary/candidates/capture",
        headers={"x-realytics-user-id": "term-reviewer"},
        json={
            "annotations": [
                {"surface": "완전히 새로운 표현", "status": "unmapped_candidate", "confidence": 0.0}
            ],
        },
    )
    candidate = captured.json()["candidates"][0]

    # No termId and no AI suggestion -> merge must be rejected, not guessed.
    merged = client.post(
        f"/glossary/candidates/{candidate['candidateId']}/review",
        headers={"x-realytics-user-id": "term-reviewer"},
        json={"status": "merged"},
    )
    assert merged.status_code == 400


def test_glossary_candidate_free_text_resolution(monkeypatch, tmp_path):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "glossary-freetext.sqlite3"))
    client = TestClient(app)

    captured = client.post(
        "/glossary/candidates/capture",
        headers={"x-realytics-user-id": "term-reviewer"},
        json={
            "annotations": [
                {"surface": "본건은 시공사 귀책으로 한한다", "status": "unmapped_candidate", "confidence": 0.0}
            ],
        },
    )
    candidate = captured.json()["candidates"][0]

    resolved = client.post(
        f"/glossary/candidates/{candidate['candidateId']}/review",
        headers={"x-realytics-user-id": "term-reviewer"},
        json={"status": "free_text"},
    )

    assert resolved.status_code == 200
    body = resolved.json()
    assert body["candidate"]["status"] == "free_text"
    assert body["term"] is None  # free text never creates/edits a canonical term
