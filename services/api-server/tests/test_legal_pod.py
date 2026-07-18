from use_cases.legal_pod import run_legal_pod_use_case
from storage import list_deal_state_records, list_legal_review_records, list_permit_review_records
from use_cases.legal_law_monitor import compare_law_references
from integrations.land_data_connectors import LandDataConnectorRouter


def test_legal_pod_persists_parcel_review_risks_and_pf_handoff():
    project_id = "legal-pod-test"
    result = run_legal_pod_use_case(
        project_id,
        {
            "project": {"type": "mixed_use"},
            "land": {"parcels": [{"pnu": "1111111111111111111", "parcelKey": "parcel-1"}]},
            "regulation": {},
            "schedule": {},
        },
        {"lawContext": {"connected": False, "source": "test_unconfigured", "references": []}},
    )

    assert result["legalReview"]["parcelLegalReviews"][0]["status"] == "needs_review"
    assert result["legalReview"]["mcp"]["status"] == "not_connected"
    assert result["handoff"]["toPod"] == "pf_credit"
    assert list_legal_review_records(project_id)
    assert list_permit_review_records(project_id)
    records = list_deal_state_records(project_id)
    assert any(record.get("code") == "LEGAL_PARCEL_REGISTRY_EVIDENCE" for record in records)
    assert any(record.get("recordType") == "handoff" for record in records)


def test_legal_pod_persists_law_evidence_and_lifecycle_coverage():
    project_id = "legal-law-evidence-test"
    result = run_legal_pod_use_case(
        project_id,
        {
            "project": {"type": "residential"},
            "land": {"parcels": []},
            "regulation": {"zoning": "제2종일반주거지역"},
            "schedule": {},
        },
        {"lawContext": {"connected": True, "source": "korean-law-mcp", "references": [{"lawId": "law-1", "lawName": "주택법", "article": "제15조", "effectiveDate": "2026-01-01", "url": "https://www.law.go.kr/test"}]}},
    )

    assert result["legalReview"]["legalLifecycleAssessment"]["lawCoverageStatus"] == "connected"
    assert result["legalReview"]["legalLifecycleAssessment"]["stages"]
    records = list_deal_state_records(project_id)
    law_records = [record for record in records if record.get("recordType") == "legal_evidence"]
    assert law_records
    assert law_records[0]["sourceLocator"]["effectiveDate"] == "2026-01-01"


def test_law_reference_monitor_detects_effective_date_change():
    report = compare_law_references(
        [{"lawReference": {"lawId": "law-1", "lawName": "주택법", "effectiveDate": "2026-01-01"}}],
        [{"lawId": "law-1", "lawName": "주택법", "effectiveDate": "2026-07-01"}],
    )
    assert report["status"] == "changed"
    assert report["changed"][0]["changes"][0]["field"] == "effectiveDate"


def test_legal_pod_inherits_representative_pnu_legal_dong_code_for_parcels():
    result = run_legal_pod_use_case(
        "legal-pnu-fallback-test",
        {
            "project": {"type": "mixed_use"},
            "land": {
                "pnu": "5115034022100060000",
                "parcels": [
                    {"mainLotNo": 6, "subLotNo": 0},
                    {"mainLotNo": 9, "subLotNo": 1},
                ],
            },
            "regulation": {},
            "schedule": {},
        },
        {"lawContext": {"connected": True, "references": []}},
    )
    parcel_pnus = [row["pnu"] for row in result["legalReview"]["parcelLegalReviews"]]
    assert parcel_pnus == ["5115034022100060000", "5115034022100090001"]


def test_legal_pod_reads_legacy_land_ledger_container_and_preserves_all_rows():
    result = run_legal_pod_use_case(
        "legal-land-ledger-container-test",
        {
            "project": {"type": "mixed_use"},
            "landLedger": {"summary": {"parcelCount": 2}, "parcels": [
                {"pnu": "5115034022100060000", "parcelKey": "parcel-1"},
                {"pnu": "5115034022100090001", "parcelKey": "parcel-2"},
            ]},
            "regulation": {},
            "schedule": {},
        },
        {"lawContext": {"connected": True, "references": []}},
    )
    parcels = result["legalReview"]["parcelLegalReviews"]
    assert len(parcels) == 2
    assert [row["pnu"] for row in parcels] == ["5115034022100060000", "5115034022100090001"]
    assert len(result["legalReview"]["landEnrichment"]["pnuResolutions"]) == 2


def test_missing_registry_evidence_is_pending_not_blocking_and_ledger_area_is_used():
    result = run_legal_pod_use_case(
        "legal-registry-pending-test",
        {
            "project": {"type": "mixed_use"},
            "landLedger": {
                "summary": {"totalAreaSqm": 12345.6},
                "landAcquisitionStatus": {"acquisitionAreaRatio": 1.0},
                "parcels": [{"pnu": "5115034022100060000"}],
            },
            "regulation": {},
            "schedule": {},
        },
        {"lawContext": {"connected": True, "references": []}},
    )
    legal = result["legalReview"]
    assert legal["developabilityAnalysis"]["areaLawTable"][1]["value"] == 12345.6
    registry_issue = next(item for item in result["riskIssues"] if item["code"] == "LEGAL_PARCEL_REGISTRY_EVIDENCE")
    assert registry_issue["blocking"] is False
    assert registry_issue["severity"] == "caution"


def test_legal_pod_hydrates_regulation_from_land_use_planning_evidence(monkeypatch):
    def fake_fetch(self, parcels, **kwargs):
        return {
            "pnuResolutions": [{"pnu": "5115034022100060000", "matchConfidence": 1.0}],
            "cadastralCharacteristics": [],
            "landUsePlanningRecords": [{
                "pnu": "5115034022100060000",
                "zoning": "제2종일반주거지역",
                "legalFar": 200,
                "legalBcr": 60,
                "districtUnitPlan": "미지정",
                "sourceName": "test land-use API",
                "sourceUrlOrEvidenceId": "https://example.test/land-use/5115034022100060000",
            }],
            "officialLandPrices": [],
            "registryEvidence": [],
            "diagnostics": [{"connector": "land_use_planning", "status": "fetched"}],
        }

    monkeypatch.setattr(LandDataConnectorRouter, "fetch_records_for_parcels", fake_fetch)
    result = run_legal_pod_use_case(
        "legal-land-use-hydration-test",
        {
            "project": {"type": "residential"},
            "land": {"parcels": [{"pnu": "5115034022100060000"}]},
            "regulation": {},
            "schedule": {},
        },
        {"lawContext": {"connected": True, "references": []}},
    )

    regulation = result["legalReview"]["developabilityAnalysis"]
    assert regulation["zoning"] == "제2종일반주거지역"
    assert any(row["field"] == "법정 용적률" and row["value"] == 200 for row in regulation["areaLawTable"])
    assert any(row["field"] == "법정 건폐율" and row["value"] == 60 for row in regulation["areaLawTable"])
    assert result["legalReview"]["landEnrichment"]["landUsePlanningRecords"][0]["sourceName"] == "test land-use API"
