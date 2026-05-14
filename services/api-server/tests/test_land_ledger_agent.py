from agents.land_ledger_agent import LandLedgerAgent, _parse_lot_number


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
