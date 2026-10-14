from pathlib import Path

import openpyxl
import pytest

from agents.land_ledger_agent import (
    LandLedgerAgent,
    LandLedgerParseError,
    _parse_lot_number,
    _read_workbook_rows,
    _payment_status,
)


@pytest.mark.parametrize("amount,paid,unpaid,interim,balance,status", [
    (100, 10, 0, 30, 60, "balance_due"),
    (100, 0, 10, 30, 60, "balance_due"),
    (100, None, 100, 0, 0, "deposit_due"),
    (100, 10, 0, 90, 0, "interim_due"),
    (100, 100, 0, 0, 0, "fully_paid"),
    (100, 100, 0, 0, None, "payment_unknown"),
    (100, 100, 0, 0, 90, "payment_needs_review"),
    (100, 110, 0, 0, 0, "payment_needs_review"),
    (100, 0, 0, 0, 0, "payment_not_started"),
    (100, None, None, None, None, "payment_unknown"),
    (None, 10, 0, 30, 60, "payment_unknown"),
    (0, 0, 0, 0, 0, "payment_unknown"),
    (100, -10, 0, 30, 80, "payment_needs_review"),
])
def test_payment_classification_requires_recorded_payment(amount, paid, unpaid, interim, balance, status):
    assert _payment_status({"individualContractAmount": amount, "depositPaid": paid,
                            "depositUnpaid": unpaid, "interimPayment": interim, "balancePayment": balance,
                            "balanceDueDate": "2000-01-01"}) == status


def test_partial_owner_payment_cannot_be_promoted_by_total_or_enrichment():
    agent = LandLedgerAgent()
    rows = [[], [], [], [],
            [1, "합성동", "1", "대", 100, 30.25, "A(1/2)", 100, 200, 100, 0, 0, 0],
            [None, None, None, None, None, None, "B(1/2)", 100, None, None, 10, 0, 90]]
    parsed = agent.analyze_rows(rows)
    for result in (parsed, agent._rebuild_analysis(parsed)):
        parcel = result["parcels"][0]
        assert parcel["paymentStatus"] == "balance_due"
        assert parcel["paymentSummary"]["knownRecordedPaidAmount"] == 100
        assert parcel["paymentSummary"]["recordedPaidMissingCount"] == 1
        assert result["landAcquisitionStatus"]["balancePaidParcels"] == 0


def test_modern_unlabelled_numeric_columns_are_not_payment_evidence():
    rows = [["title"], ["번호", "지번", "지목", "현 소유자", "지적 면적"], [], [],
            [1, "1", "대", "합성 소유자", 100, 999, 999, 999, 999, 999, 999, 999, 999]]
    result = LandLedgerAgent().analyze_rows(rows)
    owner = result["parcels"][0]["owners"][0]
    assert owner["depositPaid"] is None
    assert owner["individualContractAmount"] is None
    assert result["parcels"][0]["paymentStatus"] == "payment_unknown"
    assert result["landAcquisitionStatus"]["paidDepositParcels"] == 0


def test_pilot_adapter_preserves_payment_meaning_and_missing_records(tmp_path, monkeypatch):
    import importlib
    import sys
    # The API/CI install need not include the optional Word extraction library.
    monkeypatch.setitem(sys.modules, "docx", None)
    from scripts import update_pilot_input_from_samples as pilot_adapter
    importlib.reload(pilot_adapter)
    path = tmp_path / "joint.xlsx"
    _joint_contract_workbook(path)
    result = pilot_adapter._extract_land_ledger(path)
    parcels = result["landAcquisition"]
    assert parcels[0]["depositPaid"] == 0
    assert parcels[0]["balanceDue"] == 67.5
    assert all(parcel["paymentStatus"] != "fully_paid" for parcel in parcels)
    assert parcels[0]["paymentSummary"]["knownScheduledAmount"] == 75


def test_land_ledger_agent_parses_legacy_bonri_rows_and_builds_qa():
    land = "\ud1a0\uc9c0"
    building = "\uac74\ubb3c"
    site = "\ub300"
    total = "\ud569  \uacc4"
    rows = [
        ["Bonri mixed-use land ledger", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
        [43728, "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
        ["seq", "dong", "lot", "category", "area_sqm", "", "owner", "amount", "", "deposit", "", "interim", "balance", "holder", "bank", "account", "contract_date", "deposit_due", "balance_due", "remarks"],
        ["", "dong", "lot", "", "sqm", "py", "", "individual_amount", "parcel_total", "paid", "unpaid", "", "", "", "", "", "", "", "", ""],
        [1, "Bonri", "1-3", site, 735.5, 222.48875, f"Owner A({land})", 4_895_000_000, 5_720_000_000, "", 489_500_000, 0, 4_405_500_000, "Owner A", "IBK", "243-014339-02-038", "19.7.3", "19.9.30", "", "handover"],
        ["", "", "", "", "", "", f"Owner B({building})", 825_000_000, "", "", 82_500_000, 0, 742_500_000, "Owner B", "IBK", "243-000192-02-051", "19.7.3", "", "", ""],
        [2, "Bonri", "1-4", site, 1072.6, 324.4615, "Owner C(1/5)", 2_500_000_000, 5_000_000_000, "", 250_000_000, 0, 2_250_000_000, "Owner C", "Post", "703660-02-026021", "19.8.28", "19.9.30", "after permit", ""],
        ["", "", "", "", "", "", "Owner D(1/5)", 2_500_000_000, "", "", 250_000_000, 0, 2_250_000_000, "Owner D", "DGB", "050-08-119753", "", "", "", ""],
        [total, "", "", "", 1808.1, 546.95025, "unit price", "", 10_720_000_000, "", "", "", "", "", "", "", "", "", "", ""],
    ]

    result = LandLedgerAgent().analyze_rows(rows, sheet_name="190927 land ledger", source_file="sample.xls")

    assert result["profile"] == "legacy-bonri-land-ledger-v1"
    assert result["summary"]["parcelCount"] == 2
    assert result["summary"]["ownerRecordCount"] == 4
    assert result["summary"]["missingPnuCount"] == 2
    assert result["summary"]["registryEvidenceMissingCount"] == 2
    assert result["summary"]["workflowStatusCounts"]["needs_review"] == 2
    assert result["importConsistency"]["status"] == "passed"
    assert result["parcels"][0]["addressFragment"] == "Bonri 1-3"
    assert result["parcels"][0]["rawLotNumber"] == "1-3"
    assert result["parcels"][0]["mainLotNo"] == 1
    assert result["parcels"][0]["subLotNo"] == 3
    assert result["parcels"][0]["normalizedLotNumber"] == "1-3"
    assert result["parcels"][0]["standardLandCategoryCode"] == "08"
    assert result["parcels"][0]["owners"][0]["assetHint"] == "land"
    assert result["parcels"][0]["owners"][1]["assetHint"] == "building"
    assert result["parcels"][0]["owners"][0]["accountNumberMasked"] == "****2038"
    assert result["parcels"][0]["qa"]["sourceReconciled"] is True
    assert result["parcels"][0]["qa"]["paymentScheduleValid"] is True
    assert result["parcels"][0]["qa"]["ownershipShareValid"] is False
    assert result["parcels"][0]["qa"]["specialParcelException"] == "separate_building_owner"
    assert result["parcels"][0]["workflowStatus"] == "needs_review"
    assert result["parcels"][1]["knownShareSum"] == 0.4
    assert result["parcels"][1]["qa"]["ownershipShareValid"] is False
    assert result["validation"]["status"] == "needs_enrichment"
    assert result["landAcquisitionStatus"]["totalRequiredParcels"] == 2
    assert result["landAcquisitionStatus"]["paidDepositParcels"] == 0
    assert result["landAcquisitionStatus"]["balancePaidParcels"] == 0
    assert result["parcels"][0]["paymentStatus"] == "balance_due"
    assert result["parcels"][0]["paymentSummary"]["knownRecordedPaidAmount"] is None
    assert result["pfSummary"]["status"] == "needs_enrichment"
    assert result["qaProcess"][0]["key"] == "source_reconciliation"
    assert result["qaProcess"][4]["key"] == "pnu_confidence"
    assert result["enrichmentPlan"][0]["step"] == "resolve_pnu"
    assert "needs_review" in result["supportedWorkflowStatuses"]
    assert result["parcels"][0]["pfReviewMapping"]["legalPermitReview"]["requiresPnuResolution"] is True


def test_land_ledger_lot_number_normalization_handles_korean_variants():
    cases = {
        "142": (False, 142, 0, "142"),
        "142-1": (False, 142, 1, "142-1"),
        "\uc0b0 142": (True, 142, 0, "\uc0b0142"),
        "\uc0b0142-1": (True, 142, 1, "\uc0b0142-1"),
        "\ubcf8\ub9ac\ub3d9 142\ubc88\uc9c0": (False, 142, 0, "142"),
        "\ubcf8\ub9ac\ub3d9 142-1\ubc88\uc9c0": (False, 142, 1, "142-1"),
        "\ubcf8\ub9ac\ub3d9 \uc0b0142-1": (True, 142, 1, "\uc0b0142-1"),
    }

    for raw, expected in cases.items():
        parsed = _parse_lot_number(raw)
        assert (
            parsed["isMountain"],
            parsed["mainLotNo"],
            parsed["subLotNo"],
            parsed["normalizedLotNumber"],
        ) == expected


def test_land_ledger_detects_modern_header_layout():
    rows = [
        ["title"],
        ["번호", "지번(분할전)", "지번(분할후)", "지목", "현 소유자 (위탁자)", "지적 면적"],
        [None] * 6,
        [None] * 6,
        [1, None, "6", "답", "상갈피에프브이(주)", 3788],
    ]
    result = LandLedgerAgent().analyze_rows(rows, sheet_name="상갈동", source_file="112-parcels.xlsx")
    parcel = result["parcels"][0]
    assert result["summary"]["parcelCount"] == 1
    assert parcel["lotNumber"] == "6"
    assert parcel["areaSqm"] == 3788
    assert parcel["areaPy"] == pytest.approx(3788 * 0.3025)
    assert parcel["owners"][0]["ownerName"] == "상갈피에프브이(주)"


def test_land_ledger_batch_enrichment_resolves_pnu_price_and_registry_evidence():
    site = "\ub300"
    total = "\ud569  \uacc4"
    rows = [
        ["Bonri single parcel land ledger", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
        [43728, "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
        ["seq", "dong", "lot", "category", "area_sqm", "", "owner", "amount", "", "deposit", "", "interim", "balance", "holder", "bank", "account", "contract_date", "deposit_due", "balance_due", "remarks"],
        ["", "dong", "lot", "", "sqm", "py", "", "individual_amount", "parcel_total", "paid", "unpaid", "", "", "", "", "", "", "", "", ""],
        [1, "Bonri", "1-3", site, 100.0, 30.25, "Owner A", 1_000_000_000, 1_000_000_000, 100_000_000, 0, 300_000_000, 600_000_000, "Owner A", "IBK", "243-014339-02-038", "19.7.3", "19.9.30", "", ""],
        [total, "", "", "", 100.0, 30.25, "unit price", "", 1_000_000_000, 100_000_000, 0, 300_000_000, 600_000_000, "", "", "", "", "", "", ""],
    ]

    agent = LandLedgerAgent()
    imported = agent.analyze_rows(rows, sheet_name="land ledger", source_file="sample.xls")
    assert imported["pfSummary"]["status"] == "needs_enrichment"

    enriched = agent.enrich_analysis(
        imported,
        {
            "legalDongCode": "2729012300",
            "cadastralCharacteristics": [
                {
                    "addressFragment": "Bonri 1-3",
                    "landCategory": site,
                    "standardLandCategoryCode": "08",
                    "areaSqm": 100.0,
                    "zoning": "general_commercial",
                    "landUseZones": ["urban_area"],
                    "sourceName": "manual cadastral fixture",
                    "sourceUrlOrEvidenceId": "cad-fixture-001",
                    "baseDate": "2026-01-01",
                }
            ],
            "officialLandPrices": [
                {
                    "addressFragment": "Bonri 1-3",
                    "pricePerSqm": 2_500_000,
                    "baseYear": 2026,
                    "baseDate": "2026-01-01",
                    "sourceName": "official land price fixture",
                    "sourceUrlOrEvidenceId": "olp-fixture-001",
                }
            ],
            "registryEvidence": [
                {
                    "addressFragment": "Bonri 1-3",
                    "evidenceId": "registry-fixture-001",
                    "sourceName": "registry fixture",
                    "issuedAt": "2026-05-01",
                    "registeredOwner": "Owner A",
                    "ownerMatchStatus": "matched",
                    "mortgageExists": False,
                    "seizureExists": False,
                    "provisionalRegistrationExists": False,
                    "easementExists": False,
                    "trustRegistrationExists": True,
                    "verificationStatus": "attached_pending_legal_review",
                }
            ],
        },
    )

    parcel = enriched["parcels"][0]
    assert parcel["pnu"] == "2729012300100010003"
    assert parcel["pnuResolveConfidence"] == 0.95
    assert parcel["officialLandPricePerSqm"] == 2_500_000
    assert parcel["officialLandPriceBaseYear"] == 2026
    assert parcel["officialTotalLandValue"] == 250_000_000
    assert parcel["registryEvidence"]["ownerMatchStatus"] == "matched"
    assert parcel["cadastralCharacteristics"]["zoning"] == "general_commercial"
    assert parcel["workflowStatus"] == "ready_for_pf_review"
    assert enriched["summary"]["missingPnuCount"] == 0
    assert enriched["summary"]["missingOfficialLandPriceCount"] == 0
    assert enriched["summary"]["registryEvidenceMissingCount"] == 0
    assert enriched["pfSummary"]["status"] == "ready_for_pf_review"
    assert enriched["enrichmentRun"]["status"] == "completed"
    assert any(evidence["evidenceType"] == "official_land_price" for evidence in parcel["evidence"])


def _write_ledger_xlsx(path: Path) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "토지조서"
    sheet.append(["용인 토지조서"])
    sheet.append([])
    sheet.append(["연번", "지번", "지목", "면적", "매입금액", "지급", "소유자"])
    sheet.append([])
    sheet.append([1, "상갈동 13", "대", 1000, 500_000_000, "", "홍길동"])
    sheet.append([2, "상갈동 14", "대", 2000, 800_000_000, "", "김철수"])
    workbook.save(str(path))


def test_read_workbook_rows_supports_xlsx(tmp_path):
    xlsx = tmp_path / "ledger.xlsx"
    _write_ledger_xlsx(xlsx)

    result = LandLedgerAgent().analyze_xls(str(xlsx), {"original_file_name": "ledger.xlsx"})

    assert result["analysisType"] == "land_ledger_import"
    assert result["summary"]["parcelCount"] == 2
    assert result["sheetName"] == "토지조서"


def test_read_workbook_rows_rejects_empty_file(tmp_path):
    empty = tmp_path / "empty.xlsx"
    empty.write_bytes(b"")

    with pytest.raises(LandLedgerParseError):
        _read_workbook_rows(empty)


def test_read_workbook_rows_rejects_corrupt_workbook(tmp_path):
    corrupt = tmp_path / "corrupt.xlsx"
    corrupt.write_bytes(b"this is not a real workbook")

    with pytest.raises(LandLedgerParseError):
        _read_workbook_rows(corrupt)


def _joint_contract_workbook(path, *, merge=True, missing_individual=True):
    """Synthetic owners and parcels; only the merged-contract layout is real."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "합성 토지조서"
    for row in [
        ["Synthetic ledger"], [], [], [],
        [1, "합성동", "1", "대", 100, 30.25, "A(1/5)", 25, 125, 0, 2.5, 0, 22.5],
        [None, None, None, None, None, None, "B(1/5)", 25, None, 0, 2.5, 0, 22.5],
        [None, None, None, None, None, None, "C(1/5)", 25, None, 0, 2.5, 0, 22.5],
        [2, "합성동", "2", "대", 100, 30.25, "D(1/5)", 25, None, 0, 2.5, 0, 22.5],
        [None, None, None, None, None, None, "E(1/5)", 25, None, 0, 2.5, 0, 22.5],
        [3, "합성동", "3", "대", 100, 30.25, "F", None if missing_individual else 30, 30, 0, 3, 0, 27],
        ["합계", None, None, None, 300, 90.75, None, None, 155, 0, 15.5, 0, 139.5],
    ]:
        sheet.append(row)
    if merge:
        sheet.merge_cells("I5:I9")
    workbook.save(path)


def test_merged_contract_is_counted_once_without_parcel_allocation(tmp_path):
    path = tmp_path / "joint.xlsx"
    _joint_contract_workbook(path)
    result = LandLedgerAgent().analyze_workbook(path)
    group = result["contractGroups"][0]
    assert group["parcelSequences"] == [1, 2]
    assert group["sourceLocator"]["range"] == "I5:I9"
    assert group["totalContractAmount"] == 125
    assert group["knownShareSum"] == pytest.approx(1)
    assert group["amountReconciliation"]["passed"] is True
    assert [parcel["totalContractAmount"] for parcel in result["parcels"]] == [None, None, 30]
    assert result["parcels"][0]["source"]["rawCells"]["totalContractAmount"] == 125
    assert result["summary"]["totalContractAmount"] == 155
    assert result["summary"]["individualAmountMissingCount"] == 1
    assert result["importConsistency"]["failedChecks"] == []
    assert result["importConsistency"]["incompleteChecks"][0]["diff"] == 30
    assert result["validation"]["status"] == "needs_review"
    for parcel in result["parcels"][:2]:
        codes = {issue["code"] for issue in parcel["issues"]}
        assert "contract_group_allocation_pending" in codes
        assert "payment_schedule_inconsistent" not in codes
        assert parcel["qa"]["pfOutputReady"] is False
    # Enrichment re-build must preserve the shared amount and unresolved detail.
    rebuilt = LandLedgerAgent()._rebuild_analysis(result)
    assert rebuilt["summary"]["totalContractAmount"] == 155
    assert rebuilt["contractGroups"] == [group]


def test_blank_contract_cell_without_merge_is_not_inferred(tmp_path):
    path = tmp_path / "unmerged.xlsx"
    _joint_contract_workbook(path, merge=False)
    result = LandLedgerAgent().analyze_workbook(path)
    assert result["contractGroups"] == []
    assert result["parcels"][1]["totalContractAmount"] is None
    assert "parcel_contract_amount_missing" in {issue["code"] for issue in result["parcels"][1]["issues"]}
    assert "payment_schedule_inconsistent" in {issue["code"] for issue in result["parcels"][0]["issues"]}


def test_joint_contract_mismatch_not_hidden_by_other_missing_details(tmp_path):
    path = tmp_path / "mismatch.xlsx"
    _joint_contract_workbook(path)
    workbook = openpyxl.load_workbook(path)
    workbook.active["H5"] = 20
    workbook.save(path)
    result = LandLedgerAgent().analyze_workbook(path)
    assert result["contractGroups"][0]["amountReconciliation"]["passed"] is False
    assert result["importConsistency"]["failedChecks"][0]["name"] == "contract_group:I5:I9"
    assert result["importConsistency"]["incompleteChecks"]


def test_land_upload_and_budget_candidate_survive_repository_reload(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from main import app
    from routers import projects
    from use_cases.land_ledger_import import reconcile_land_costs_use_case
    from use_cases.workbook_import_approval import persist_feasibility_workbook_candidate_use_case

    path = tmp_path / "joint.xlsx"
    _joint_contract_workbook(path)
    client = TestClient(app)
    project_id = client.post("/projects/", json={
        "project": {"name": "Synthetic land source review"},
        "feasibility_input": {"land_cost": 150, "total_revenue": 300},
    }).json()["project"]["id"]
    monkeypatch.setattr(projects, "_maybe_auto_reanalyze", lambda *args: {"triggered": False})
    response = client.post(f"/projects/{project_id}/land-ledger/analyze", files={"file": (path.name, path.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert response.status_code == 200, response.text
    ledger = response.json()["landLedger"]
    assert ledger["contractGroups"][0]["totalContractAmount"] == 125
    # Existing candidate persistence preserves evidence without approving inputs.
    repository = projects.project_repository()
    before = repository.get(project_id)
    draft = {"raw_import": {"validationStatus": "failed", "landCostBreakdown": {
        "purchaseBudget": {"value": 160, "unit": "KRW", "excel": {"sheet_name": "사업수지", "cell": "D19"}},
        "inclusiveBudget": {"value": 170, "unit": "KRW", "excel": {"sheet_name": "사업수지", "cell": "D26"}},
    }}}
    persist_feasibility_workbook_candidate_use_case(repository, project_id, {"draft": draft}, recorded_by="test-reviewer")
    reloaded = repository.get(project_id)
    assert reloaded["landLedger"]["contractGroups"] == ledger["contractGroups"]
    assert reloaded["landLedger"]["parcels"][0]["paymentReviewVersion"] == "ledger-recorded-payments-v1"
    assert reloaded["landLedger"]["parcels"][0]["paymentStatus"] == "payment_unknown"
    assert reloaded["landLedger"]["landAcquisitionStatus"]["balancePaidParcels"] == 0
    assert reloaded.get("feasibility_input") == before.get("feasibility_input")
    assert reloaded.get("canonicalFeasibilityStudyV2") == before.get("canonicalFeasibilityStudyV2")
    comparison = reconcile_land_costs_use_case(reloaded)
    assert comparison["ledgerContract"]["value"] == 155
    assert comparison["ledgerContract"]["sourceLocator"]["range"] == "I5:I10"
    assert comparison["ledgerContract"]["sourceTotalLocator"]["cell"] == "I11"
    assert comparison["purchaseMinusContractKrw"] == 5
    assert comparison["ancillaryBudgetKrw"] == 10
    context = client.get(f"/projects/{project_id}/workspace/context?page=land&track=pf")
    assert context.status_code == 200, context.text
    assert context.json()["pageData"]["landCostReconciliation"] == comparison
    assert context.json()["pageData"]["landLedger"]["parcels"] == ledger["parcels"]
    from storage import get_project_record, upsert_user
    upsert_user({"userId": "land-reviewer", "tenantId": "pytest-tenant", "role": "admin", "status": "active"})
    headers = {"x-realytics-user-id": "land-reviewer"}
    saved = client.put(f"/projects/{project_id}/land-cost-review", headers=headers, json={
        "comment": "계약과 예산의 기준일 확인 필요", "expectedComparisonHash": comparison["comparisonHash"],
        "reviewedBy": "forged-client-user",
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["review"]["reviewedBy"] == "land-reviewer"
    durable = get_project_record(project_id)
    assert durable["landCostReview"]["comment"] == "계약과 예산의 기준일 확인 필요"
    assert durable["landLedger"]["contractGroups"] == ledger["contractGroups"]
    reopened = client.get(f"/projects/{project_id}/workspace/context?page=land&track=pf").json()["pageData"]["landCostReconciliation"]
    assert reopened["reviewMatchesCurrentSources"] is True
    assert reopened["review"]["comment"] == "계약과 예산의 기준일 확인 필요"
    assert len(reopened["review"]["reviewHistory"]) == 1
    assert reopened["comparisonHash"] == comparison["comparisonHash"]
    empty = client.put(f"/projects/{project_id}/land-cost-review", headers=headers, json={"comment": " ", "expectedComparisonHash": comparison["comparisonHash"]})
    assert empty.status_code == 400
    # New source basis invalidates the old note without deleting its history.
    draft["raw_import"]["landCostBreakdown"]["purchaseBudget"]["value"] = 165
    persist_feasibility_workbook_candidate_use_case(repository, project_id, {"draft": draft}, recorded_by="test-reviewer")
    changed = reconcile_land_costs_use_case(repository.get(project_id))
    assert changed["reviewMatchesCurrentSources"] is False
    assert changed["review"]["comment"] == "계약과 예산의 기준일 확인 필요"
    stale = client.put(f"/projects/{project_id}/land-cost-review", headers=headers, json={"comment": "stale", "expectedComparisonHash": comparison["comparisonHash"]})
    assert stale.status_code == 409
    assert repository.get(project_id).get("feasibility_input") == before.get("feasibility_input")
    upsert_user({"userId": "land-viewer", "tenantId": "pytest-tenant", "role": "viewer", "status": "active"})
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "strict")
    denied = client.put(f"/projects/{project_id}/land-cost-review", headers={"x-realytics-user-id": "land-viewer"}, json={"comment": "not allowed", "expectedComparisonHash": changed["comparisonHash"]})
    assert denied.status_code == 403
    forged = client.post("/projects/", headers=headers, json={"project": {"name": "forged"}, "landCostReview": {"comment": "forged"}})
    assert forged.status_code == 400
    repository.update(project_id, {"_meta": {"tenantId": "pytest-tenant"}})
    upsert_user({"userId": "foreign-land-reviewer", "tenantId": "foreign-tenant", "role": "reviewer", "status": "active"})
    monkeypatch.setenv("REALYTICS_TENANT_ISOLATION_MODE", "strict")
    foreign = client.put(f"/projects/{project_id}/land-cost-review", headers={"x-realytics-user-id": "foreign-land-reviewer"}, json={"comment": "foreign", "expectedComparisonHash": changed["comparisonHash"]})
    assert foreign.status_code in {403, 404}


def test_missing_parcel_prices_do_not_become_a_zero_contract_total():
    from use_cases.land_ledger_import import reconcile_land_costs_use_case

    result = reconcile_land_costs_use_case({"landLedger": {
        "parcels": [{"sequence": 1, "totalContractAmount": None}], "summary": {"totalContractAmount": 0},
    }})
    assert result["ledgerContract"]["value"] is None
    assert result["purchaseMinusContractKrw"] is None


@pytest.mark.parametrize("value, expected", [(None, None), (0, -155), (150, -5)])
def test_land_comparison_preserves_missing_zero_and_negative_difference(tmp_path, value, expected):
    from use_cases.land_ledger_import import reconcile_land_costs_use_case

    path = tmp_path / "joint.xlsx"
    _joint_contract_workbook(path)
    ledger = LandLedgerAgent().analyze_workbook(path)
    comparison = reconcile_land_costs_use_case({"landLedger": ledger, "feasibilityStudyV2Draft": {"raw_import": {
        "landCostBreakdown": {"purchaseBudget": {"value": value}, "inclusiveBudget": {"value": 170}},
    }}})
    assert comparison["purchaseMinusContractKrw"] == expected
    assert comparison["status"] == ("incomplete" if value is None else "needs_review")
