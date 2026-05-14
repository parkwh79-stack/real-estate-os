from agents.land_ledger_agent import LandLedgerAgent


def test_land_ledger_agent_parses_legacy_bonri_rows_and_builds_qa():
    land = "\ud1a0\uc9c0"
    building = "\uac74\ubb3c"
    total = "\ud569  \uacc4"
    rows = [
        ["Bonri mixed-use land ledger", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
        [43728, "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
        ["seq", "dong", "lot", "category", "area_sqm", "", "owner", "amount", "", "deposit", "", "interim", "balance", "holder", "bank", "account", "contract_date", "deposit_due", "balance_due", "remarks"],
        ["", "dong", "lot", "", "sqm", "py", "", "individual_amount", "parcel_total", "paid", "unpaid", "", "", "", "", "", "", "", "", ""],
        [1, "Bonri", "1-3", "site", 735.5, 222.48875, f"Owner A({land})", 4_895_000_000, 5_720_000_000, "", 489_500_000, 0, 4_405_500_000, "Owner A", "IBK", "243-014339-02-038", "19.7.3", "19.9.30", "", "handover"],
        ["", "", "", "", "", "", f"Owner B({building})", 825_000_000, "", "", 82_500_000, 0, 742_500_000, "Owner B", "IBK", "243-000192-02-051", "19.7.3", "", "", ""],
        [2, "Bonri", "1-4", "site", 1072.6, 324.4615, "Owner C(1/5)", 2_500_000_000, 12_500_000_000, "", 250_000_000, 0, 2_250_000_000, "Owner C", "Post", "703660-02-026021", "19.8.28", "19.9.30", "after permit", ""],
        ["", "", "", "", "", "", "Owner D(1/5)", 2_500_000_000, "", "", 250_000_000, 0, 2_250_000_000, "Owner D", "DGB", "050-08-119753", "", "", "", ""],
        [total, "", "", "", 1808.1, 546.95025, "unit price", "", 18_220_000_000, "", "", "", "", "", "", "", "", "", "", ""],
    ]

    result = LandLedgerAgent().analyze_rows(rows, sheet_name="190927 land ledger", source_file="sample.xls")

    assert result["profile"] == "legacy-bonri-land-ledger-v1"
    assert result["summary"]["parcelCount"] == 2
    assert result["summary"]["ownerRecordCount"] == 4
    assert result["summary"]["missingPnuCount"] == 2
    assert result["summary"]["registryEvidenceMissingCount"] == 2
    assert result["summary"]["workflowStatusCounts"]["parcel_validated"] == 2
    assert result["parcels"][0]["addressFragment"] == "Bonri 1-3"
    assert result["parcels"][0]["owners"][0]["assetHint"] == "land"
    assert result["parcels"][0]["owners"][1]["assetHint"] == "building"
    assert result["parcels"][0]["owners"][0]["accountNumberMasked"] == "****2038"
    assert result["parcels"][0]["qa"]["sourceReconciled"] is True
    assert result["parcels"][0]["qa"]["paymentScheduleValid"] is True
    assert result["parcels"][0]["qa"]["ownershipShareValid"] is False
    assert result["parcels"][0]["qa"]["specialParcelException"] == "separate_building_owner"
    assert result["parcels"][0]["workflowStatus"] == "parcel_validated"
    assert result["parcels"][1]["knownShareSum"] == 0.4
    assert result["parcels"][1]["qa"]["ownershipShareValid"] is False
    assert result["validation"]["status"] == "needs_enrichment"
    assert result["qaProcess"][0]["key"] == "source_reconciliation"
    assert result["qaProcess"][4]["key"] == "pnu_confidence"
    assert result["enrichmentPlan"][0]["step"] == "resolve_pnu"
    assert result["parcels"][0]["pfReviewMapping"]["legalPermitReview"]["requiresPnuResolution"] is True
