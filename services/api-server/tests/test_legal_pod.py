import pytest

from agents.korean_law_context import build_server_issued_law_context
from use_cases.legal_pod import _reusable_public_record, run_legal_pod_use_case
from storage import (
    list_deal_state_records,
    list_land_evidence_records,
    list_legal_review_records,
    list_permit_review_records,
    list_reusable_public_land_evidence_records,
    upsert_land_evidence_record,
)
from use_cases.legal_law_monitor import compare_law_references
from integrations.land_data_connectors import LandDataConnectorRouter


@pytest.fixture(autouse=True)
def _isolate_legal_pod_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "legal-pod.sqlite3"))


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


def test_permit_review_requires_approved_source_located_parser_evidence():
    project_id = "legal-permit-evidence-test"
    base_project = {
        "project": {"id": project_id, "type": "mixed_use"},
        "land": {},
        "regulation": {},
        "schedule": [{"key": "permit_approval", "planned_date": "2026-10"}],
    }

    missing = run_legal_pod_use_case(
        project_id,
        base_project,
        {"autoEnrichLand": False},
    )["permitReview"]
    assert missing["status"] == "pending_evidence"
    assert missing["parserStatus"] == "not_available"
    assert missing["verifiedEvidenceCount"] == 0
    assert missing["sourceLocators"] == []
    assert missing["permitRouteReview"]["plannedDate"] == "2026-10"
    assert missing["permitRouteReview"]["approvalEvidenceStatus"] == "missing"

    verified = run_legal_pod_use_case(
        project_id,
        base_project,
        {
            "autoEnrichLand": False,
            "parsedDocuments": [
                {
                    "documentType": "building_permit",
                    "verificationStatus": "approved",
                    "sourceLocator": {
                        "sourceType": "document_page",
                        "sourceDocumentId": "permit-document-1",
                        "pageNumber": 4,
                        "excerpt": "Permit approval condition",
                    },
                },
                {
                    "documentType": "building_permit",
                    "verificationStatus": "candidate",
                    "sourceLocator": {
                        "sourceDocumentId": "unapproved-document",
                        "pageNumber": 1,
                    },
                },
            ],
        },
    )["permitReview"]
    assert verified["parserStatus"] == "verified"
    assert verified["evidenceStatus"] == "verified"
    assert verified["verifiedEvidenceCount"] == 1
    assert verified["sourceLocators"][0]["sourceDocumentId"] == (
        "permit-document-1"
    )
    assert verified["permitRouteReview"]["approvalEvidenceStatus"] == "source_review_required"
    assert verified["permitRouteReview"]["approvalDocumentLocators"][0]["route"] == "building_permit"


def test_officetel_approval_candidates_require_matching_document_type_and_source():
    def document(kind, page=1, status="approved"):
        return {
            "documentType": kind,
            "verificationStatus": status,
            "sourceLocator": {"sourceDocumentId": kind, "pageNumber": page},
        }

    result = run_legal_pod_use_case(
        "officetel-permit-review",
        {"project": {"type": "오피스텔"}, "schedule": {"permit_approval": "2026-10"}},
        {
            "autoEnrichLand": False,
            "parsedDocuments": [
                document("building_permit_application"),
                document("housing_business_plan_approval"),
                document("building_permit", page=None),
                document("building_permit", status="candidate"),
            ],
        },
    )
    review = result["permitReview"]["permitRouteReview"]
    assert review["useClassification"] == "officetel"
    assert [route["code"] for route in review["routes"]] == ["building_permit"]
    assert review["approvalEvidenceStatus"] == "missing"
    assert review["approvalDocumentLocators"] == []


def test_legal_pod_persists_law_evidence_and_lifecycle_coverage(monkeypatch):
    import use_cases.legal_pod as legal_pod

    verified_reference = {
        "type": "statute",
        "lawId": "law-1",
        "lawName": "주택법",
        "title": "주택법",
        "citation": "제15조",
        "article": "제15조",
        "effectiveDate": "2026-01-01",
        "url": "https://www.law.go.kr/test",
        "locator": "https://www.law.go.kr/test",
        "sourceAuthority": "law_go_kr",
        "transport": "local_verified_snapshot",
        "verificationStatus": "verified_snapshot",
        "resourceId": "legal-resource:law-1",
        "resourceVersionId": "legal-resource-version:law-1:2026",
        "officialVersionId": "law-1-2026-01-01",
        "sourceFetchId": "legal-source-fetch:law-1:2026",
        "provisionId": "legal-provision:law-1:15",
        "normalizedContentSha256": "sha256:" + ("b" * 64),
        "contentHash": "sha256:" + ("a" * 64),
        "asOfDate": "2026-07-18",
        "requestedAsOfDate": "2026-07-18",
        "verifiedEffectiveDate": "2026-01-01",
        "effectiveFrom": "2026-01-01",
        "effectiveTo": "2026-12-31",
        "observedAt": "2026-07-18T00:00:00+00:00",
        "fetchedAt": "2026-07-18T00:00:00+00:00",
        "verifiedAt": "2026-07-18T00:00:01+00:00",
        "verified": True,
        "rawTool": "legal_intelligence_core",
    }
    monkeypatch.setattr(
        legal_pod,
        "resolve_project_legal_context",
        lambda project_data, caller_context=None: {
            "lawContext": build_server_issued_law_context({
                "version": "realytics-legal-intelligence-v1",
                "source": "legal-intelligence-core",
                "connected": False,
                "retrievedAt": "2026-07-18T00:00:02+00:00",
                "references": [verified_reference],
                "citationVerification": {
                    "status": "verified",
                    "checkedCount": 1,
                    "verifiedCount": 1,
                    "failures": [],
                },
            }),
            "legalIntelligence": {
                "schemaVersion": "realytics-legal-intelligence-v1",
                "evidenceStatus": "verified",
                "verifiedReferenceCount": 1,
                "candidateReferenceCount": 1,
                "stale": False,
                "openConflicts": False,
                "humanReviewStatus": "pending",
                "references": [verified_reference],
                "candidateReferences": [],
                "citationVerification": {
                    "status": "verified",
                    "checkedCount": 1,
                    "failures": [],
                },
                "providerTrace": [],
            },
        },
    )
    project_id = "legal-law-evidence-test"
    result = run_legal_pod_use_case(
        project_id,
        {
            "project": {"type": "residential"},
            "land": {"parcels": []},
            "regulation": {"zoning": "제2종일반주거지역"},
            "schedule": {},
        },
        {
            "lawContext": {
                "connected": True,
                "source": "caller-supplied",
                "references": [{"lawId": "caller-only"}],
            }
        },
    )

    assert result["legalReview"]["legalLifecycleAssessment"]["lawCoverageStatus"] == "connected"
    assert result["legalReview"]["legalLifecycleAssessment"]["stages"]
    records = list_deal_state_records(project_id)
    law_records = [record for record in records if record.get("recordType") == "legal_evidence"]
    assert law_records
    assert law_records[0]["evidenceLocator"]["effectiveDate"] == "2026-01-01"
    assert law_records[0]["sourceLocator"]["effectiveDate"] == "2026-01-01"
    assert law_records[0]["sourceLocator"]["contentHash"].startswith("sha256:")
    assert result["handoff"]["requiresHumanReview"] is True
    assert result["handoff"]["reviewStatus"] == "pending"


def test_law_reference_monitor_detects_effective_date_change():
    report = compare_law_references(
        [{"lawReference": {"lawId": "law-1", "lawName": "주택법", "effectiveDate": "2026-01-01"}}],
        [{"lawId": "law-1", "lawName": "주택법", "effectiveDate": "2026-07-01"}],
    )
    assert report["status"] == "changed"
    assert report["changed"][0]["changes"][0]["field"] == "effectiveDate"


def test_legal_pod_does_not_infer_parcel_pnus_from_a_representative_pnu():
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
    assert parcel_pnus == ["", ""]
    diagnostics = result["legalReview"]["landEnrichment"]["diagnostics"]
    assert [row["status"] for row in diagnostics] == ["skipped_missing_pnu", "skipped_missing_pnu"]


def test_legal_pod_keeps_unmatched_parcel_separate_from_existing_pnu(monkeypatch):
    pnu = "4146310300100130000"

    def fetch(self, parcels, **kwargs):
        assert [row["pnu"] for row in parcels] == [pnu]
        return {"pnuResolutions": [{"pnu": pnu}], "status": "completed"}

    monkeypatch.setattr(LandDataConnectorRouter, "fetch_records_for_parcels", fetch)
    result = run_legal_pod_use_case("legal-pnu-lineage", {
        "land_acquisition": [],
        "land_ledger": {"parcels": [
            {"parcelKey": "matched", "resolvedPnu": pnu},
            {"parcelKey": "unmatched", "lotNumber": "13"},
        ]},
    })
    rows = result["legalReview"]["parcelLegalReviews"]
    assert [(row["parcelKey"], row["pnu"]) for row in rows] == [("matched", pnu), ("unmatched", "")]


def test_land_rights_checks_ownership_share_per_parcel():
    result = run_legal_pod_use_case("legal-ownership-lineage", {
        "landLedger": {"parcels": [
            {"parcelKey": "p1", "knownShareSum": 0.6},
            {"parcelKey": "p2", "knownShareSum": 0.6},
        ]},
    }, {"autoEnrichLand": False})
    rights = next(row for row in result["legalReview"]["checks"] if row["key"] == "land_rights")
    assert rights["severity"] == "blocking"


def test_legal_pod_preview_resolves_and_reviews_detached_parcel_without_writes(monkeypatch):
    from copy import deepcopy
    from integrations import vworld
    from use_cases import legal_pod

    pnu = "4146310300100130000"
    project = {
        "project": {"type": "officetel"},
        "land": {"address": "경기도 용인시 기흥구 상갈동 13", "area_sqm": 1000},
        "landLedger": {"parcels": [{
            "lotNumber": "13",
            "areaSqm": 1000,
            "legalFar": 700,
            "landUsePlanning": {"legalBcr": 85},
        }]},
    }
    original = deepcopy(project)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Preview tried to retain a result")

    for name in (
        "upsert_deal_state_record", "upsert_legal_review_record",
        "upsert_permit_review_record", "upsert_land_evidence_record",
    ):
        monkeypatch.setattr(legal_pod, name, forbidden)
    monkeypatch.setattr(legal_pod, "vworld_configured", lambda: True)
    monkeypatch.setattr(vworld, "resolve_legal_dong_code", lambda _address: pnu[:10])
    monkeypatch.setattr(vworld, "fetch_parcel_by_pnu", lambda value: (
        {"pnu": value, "jibun": "13"}, {"status": "resolved"},
    ))

    def land_lookup(_self, parcels, **_kwargs):
        assert [parcel["pnu"] for parcel in parcels] == [pnu]
        return {
            "status": "completed",
            "landUsePlanningRecords": [{
                "pnu": pnu,
                "zoning": "제2종일반주거지역",
                "sourceUrlOrEvidenceId": "https://example.test/zoning",
            }],
            "regionLandPriceTrends": [{"regionCode": pnu[:10]}],
        }

    def law_lookup(_project, *, caller_context, preview):
        assert preview is True
        return {
            "lawContext": {"references": [], "connected": False},
            "legalIntelligence": {"references": []},
        }

    monkeypatch.setattr(LandDataConnectorRouter, "fetch_records_for_parcels", land_lookup)
    monkeypatch.setattr(legal_pod, "resolve_project_legal_context", law_lookup)
    result = run_legal_pod_use_case("legal-preview", project, persist=False)
    assert result["previewOnly"] is True
    assert result["legalReview"]["parcelLegalReviews"][0]["pnu"] == pnu
    assert result["legalReview"]["developabilityAnalysis"]["zoning"] == "제2종일반주거지역"
    rows = result["legalReview"]["developabilityAnalysis"]["areaLawTable"]
    assert all(row["value"] is None for row in rows if row["field"] in {"법정 용적률", "법정 건폐율"})
    assert result["legalReview"]["landEnrichment"]["previewParcelResolution"]["resolvedCount"] == 1
    assert project == original


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
            "land": {"area_sqm": 1000},
            "landLedger": {"parcels": [{"pnu": "5115034022100060000", "areaSqm": 1000}]},
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


def test_legal_pod_reuses_public_pnu_evidence_across_projects_without_external_fetch(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "public-pnu-reuse.sqlite3"))
    pnu = "5115034022100060000"
    connector_calls = []

    def fake_fetch(self, parcels, **kwargs):
        connector_calls.append([str(parcel.get("pnu") or "") for parcel in parcels])
        return {
            "status": "completed",
            "pnuResolutions": [{"pnu": pnu, "matchConfidence": 1.0}],
            "cadastralCharacteristics": [],
            "landUsePlanningRecords": [{
                "pnu": pnu,
                "zoning": "제2종일반주거지역",
                "legalFar": 200,
                "legalBcr": 60,
                "districtUnitPlan": "미지정",
                "sourceName": "official land-use API",
                "sourceType": "public_api",
                "sourceUrlOrEvidenceId": f"https://example.test/land-use/{pnu}",
            }],
            "buildingUseRecords": [],
            "landCharacteristicsRecords": [],
            "regionLandPriceTrends": [],
            "officialLandPrices": [{
                "pnu": pnu,
                "pricePerSqm": 1_250_000,
                "baseYear": 2026,
                "sourceName": "official land-price API",
                "sourceType": "official_public_api",
                "sourceUrlOrEvidenceId": f"https://example.test/land-price/{pnu}",
            }],
            "registryEvidence": [],
            "diagnostics": [],
        }

    def project_data(tenant_id):
        return {
            "project": {"type": "residential", "tenantId": tenant_id},
            "land": {"area_sqm": 1000},
            "landLedger": {"parcels": [{"pnu": pnu, "areaSqm": 1000}]},
            "regulation": {"applied_far": 180, "applied_bcr": 50},
            "schedule": {},
        }

    monkeypatch.setattr(LandDataConnectorRouter, "fetch_records_for_parcels", fake_fetch)
    first = run_legal_pod_use_case(
        "public-cache-source-project",
        project_data("tenant-a"),
        {"lawContext": {"connected": True, "references": []}},
    )
    assert connector_calls == [[pnu]]
    assert first["legalReview"]["landEnrichment"]["publicEvidenceCache"] == {
        "requestedPnuCount": 1,
        "reusedRecordCount": 0,
        "externalFetchParcelCount": 1,
        "reuseScope": "official_public_facts_only",
    }
    reusable = list_reusable_public_land_evidence_records([pnu])
    assert {record["evidenceType"] for record in reusable} >= {
        "land_use_planning",
        "official_land_price",
    }

    second = run_legal_pod_use_case(
        "public-cache-target-project",
        project_data("tenant-b"),
        {"lawContext": {"connected": True, "references": []}},
    )

    assert connector_calls == [[pnu]], "a complete public PNU cache hit must skip the external connector"
    enrichment = second["legalReview"]["landEnrichment"]
    assert enrichment["status"] == "cache_hit"
    assert enrichment["publicEvidenceCache"]["externalFetchParcelCount"] == 0
    assert enrichment["publicEvidenceCache"]["reusedRecordCount"] >= 2
    reused_land_use = enrichment["landUsePlanningRecords"][0]
    assert reused_land_use["cacheReuse"]["scope"] == "public_pnu_evidence"
    assert "projectId" not in reused_land_use


def test_legal_pod_does_not_reuse_private_or_tenant_scoped_pnu_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "private-pnu-isolation.sqlite3"))
    pnu = "5115034022100090001"
    source_project_id = "tenant-a-private-source"
    target_project_id = "tenant-b-target"
    manual_land_use = _reusable_public_record({
        "recordKey": f"land_use_planning:{pnu}",
        "pnu": pnu,
        "tenantId": "tenant-a",
        "sourceType": "manual_upload",
        "legalFar": 999,
        "confidentialNote": "tenant-a-only",
    }, "land_use_planning")
    manual_price = _reusable_public_record({
        "recordKey": f"official_land_price:{pnu}",
        "pnu": pnu,
        "tenantId": "tenant-a",
        "sourceType": "manual_upload",
        "pricePerSqm": 99_999_999,
        "confidentialNote": "tenant-a-only",
    }, "official_land_price")
    upsert_land_evidence_record(source_project_id, manual_land_use)
    upsert_land_evidence_record(source_project_id, manual_price)
    upsert_land_evidence_record(source_project_id, {
        "recordKey": f"registry_record:{pnu}",
        "evidenceType": "registry_record",
        "pnu": pnu,
        "tenantId": "tenant-a",
        "sourceType": "manual_upload",
        "ownerName": "private-owner",
        "publicData": False,
        "reuseEligible": False,
    })
    assert list_reusable_public_land_evidence_records([pnu]) == []

    connector_calls = []

    def fake_fetch(self, parcels, **kwargs):
        connector_calls.append([str(parcel.get("pnu") or "") for parcel in parcels])
        return {
            "status": "completed",
            "pnuResolutions": [{"pnu": pnu, "matchConfidence": 1.0}],
            "cadastralCharacteristics": [],
            "landUsePlanningRecords": [{
                "pnu": pnu,
                "zoning": "제2종일반주거지역",
                "legalFar": 200,
                "legalBcr": 60,
                "sourceName": "official land-use API",
                "sourceType": "official_public_api",
                "sourceUrlOrEvidenceId": f"https://example.test/land-use/{pnu}",
            }],
            "buildingUseRecords": [],
            "landCharacteristicsRecords": [],
            "regionLandPriceTrends": [],
            "officialLandPrices": [{
                "pnu": pnu,
                "pricePerSqm": 900_000,
                "baseYear": 2026,
                "sourceName": "official land-price API",
                "sourceType": "official_public_api",
                "sourceUrlOrEvidenceId": f"https://example.test/land-price/{pnu}",
            }],
            "registryEvidence": [],
            "diagnostics": [],
        }

    monkeypatch.setattr(LandDataConnectorRouter, "fetch_records_for_parcels", fake_fetch)
    result = run_legal_pod_use_case(
        target_project_id,
        {
            "project": {"type": "residential", "tenantId": "tenant-b"},
            "land": {"area_sqm": 1000},
            "landLedger": {"parcels": [{"pnu": pnu, "areaSqm": 1000}]},
            "regulation": {"applied_far": 180, "applied_bcr": 50},
            "schedule": {},
        },
        {"lawContext": {"connected": True, "references": []}},
    )

    assert connector_calls == [[pnu]], "private evidence must not satisfy the public-cache requirement"
    analysis = result["legalReview"]["developabilityAnalysis"]
    legal_far = next(row["value"] for row in analysis["areaLawTable"] if row["field"] == "법정 용적률")
    assert legal_far == 200
    assert result["legalReview"]["parcelLegalReviews"][0]["registryEvidenceCount"] == 0
    target_records = list_land_evidence_records(target_project_id)
    assert all(record.get("tenantId") != "tenant-a" for record in target_records)
    assert all(record.get("ownerName") != "private-owner" for record in target_records)
    assert all(record.get("confidentialNote") != "tenant-a-only" for record in target_records)


def test_public_pnu_reuse_requires_verified_source_and_untampered_payload_hash():
    unverified_pnu = "5115034022100100001"
    tampered_pnu = "5115034022100100002"
    unverified = _reusable_public_record({
        "pnu": unverified_pnu,
        "sourceType": "official_public_api",
        "sourceUrlOrEvidenceId": f"https://example.test/land-use/{unverified_pnu}",
        "legalFar": 200,
    }, "land_use_planning")
    unverified["verificationStatus"] = "attached_pending_review"
    tampered = _reusable_public_record({
        "pnu": tampered_pnu,
        "sourceType": "official_public_api",
        "sourceUrlOrEvidenceId": f"https://example.test/land-use/{tampered_pnu}",
        "legalFar": 200,
    }, "land_use_planning")
    tampered["payloadHash"] = "0" * 64
    upsert_land_evidence_record("invalid-public-cache-source", unverified)
    upsert_land_evidence_record("invalid-public-cache-source", tampered)

    assert list_reusable_public_land_evidence_records([unverified_pnu, tampered_pnu]) == []


def test_legal_pod_distinguishes_planned_height_from_parcel_designation_clues():
    project_id = "parcel-height-review"
    pnu = "2729011700100040000"
    upsert_land_evidence_record(project_id, {
        "recordKey": f"land_use_planning:{pnu}",
        "evidenceType": "land_use_planning",
        "pnu": pnu,
        "zoning": "제3종일반주거지역",
        "landUseZones": ["지구단위계획구역", "고도지구"],
        "landUsePlanItems": [
            {"designationName": "지구단위계획구역", "conflictName": "접함"},
        ],
    })
    project = {
        "landLedger": {"parcels": [{
            "pnu": pnu,
            "lotNumber": "4",
            "landUsePlanning": {"districtUnitPlan": "수기 계획 명칭"},
        }]},
        "regulation": {"plannedHeightM": 36, "maxHeightM": 30, "heightLimitVerified": True},
    }

    result = run_legal_pod_use_case(project_id, project, {"autoEnrichLand": False})

    analysis = result["legalReview"]["developabilityAnalysis"]
    height = analysis["heightReview"]
    assert height["status"] == "needs_evidence"
    assert height["jurisdictionCandidate"] == "대구광역시"
    assert height["districtPlanNames"] == ["지구단위계획구역"]
    assert height["parcelReviews"][0]["designationEvidence"][0]["relation"] == "접함"
    assert height["parcelReviews"][0]["zoning"] == "제3종일반주거지역"
    assert height["parcelReviews"][0]["sourceLocator"]["apiRowId"] == pnu
    assert height["plannedHeightM"] == 36
    assert height["candidateLimitM"] == 30
    assert height["confirmedLimitM"] is None
    assert not analysis["breaches"]
    assert project["landLedger"]["parcels"][0]["landUsePlanning"] == {"districtUnitPlan": "수기 계획 명칭"}
