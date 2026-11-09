import pytest

from domains.feasibility.regulatory import build_preliminary_buildable_envelope, evaluate_program_eligibility


def _project(*, approved=True):
    project = {
        "siteParcelSnapshotId": "parcel-snapshot-1",
        "land": {"area_sqm": 3000},
        "landLedger": {
            "parcels": [
                {
                    "pnu": "1111010100100010000",
                    "areaSqm": 1000,
                    "landUsePlanning": {"legalFar": 200, "landUseZones": ["제2종일반주거지역"]},
                },
                {
                    "pnu": "1111010100100020000",
                    "areaSqm": 2000,
                    "landUsePlanning": {"legalFar": 300, "landUseZones": ["제3종일반주거지역"]},
                },
            ]
        },
    }
    if approved:
        parcel_hash = build_preliminary_buildable_envelope(
            project,
            declared_land_area_sqm=3000,
        )["siteParcelSnapshot"]["parcelSetHash"]
        project["regulatoryConstraintSnapshot"] = {
            "snapshotId": "reg-snapshot-1",
            "approvalStatus": "approved",
            "approvedBy": "legal-reviewer",
            "approvalDecisionId": "reg-approval-1",
            "effectiveDate": "2026-07-16",
            "sourceHash": "c" * 64,
            "siteParcelSnapshotId": "parcel-snapshot-1",
            "parcelSetHash": parcel_hash,
            "parcelCount": 2,
            "includedParcelCount": 2,
            "aggregationPolicy": {
                "policyId": "parcel_capacity_sum",
                "version": "1.0.0",
                "approved": True,
            },
        }
    return project


def _verified_approval(project):
    snapshot = project["regulatoryConstraintSnapshot"]
    return {
        "approvalDecisionId": snapshot["approvalDecisionId"],
        "approvalStatus": "approved",
        "snapshotId": snapshot["snapshotId"],
        "parcelSetHash": snapshot["parcelSetHash"],
        "sourceHash": snapshot["sourceHash"],
    }


def test_parcel_weighted_capacity_never_uses_max_far_for_entire_site():
    project = _project()
    envelope = build_preliminary_buildable_envelope(
        project,
        declared_land_area_sqm=3000,
        verified_approval_decision=_verified_approval(project),
    )

    assert envelope["farCountedGfaCapacitySqm"] == 8000
    assert envelope["effectiveLegalFar"] == pytest.approx(8000 / 3000 * 100)
    assert envelope["farCountedGfaCapacitySqm"] != 9000
    assert envelope["reportGrade"] is True
    assert envelope["eligibility"] == "eligible"
    assert envelope["siteParcelSnapshot"]["pnuCoverage"] == 1.0


def test_incomplete_or_duplicate_parcel_set_is_not_assessable():
    project = _project()
    project["landLedger"]["parcels"][1]["landUsePlanning"].pop("legalFar")
    project["landLedger"]["parcels"][1]["pnu"] = project["landLedger"]["parcels"][0]["pnu"]

    envelope = build_preliminary_buildable_envelope(project, declared_land_area_sqm=3000)

    assert envelope["eligibility"] == "not_assessable"
    assert envelope["farCountedGfaCapacitySqm"] is None
    assert {"PNU_DUPLICATE", "PARCEL_LEGAL_FAR_MISSING"} <= set(envelope["blockingCodes"])


def test_unapproved_aggregation_remains_exploratory():
    envelope = build_preliminary_buildable_envelope(_project(approved=False), declared_land_area_sqm=3000)

    assert envelope["farCountedGfaCapacitySqm"] == 8000
    assert envelope["eligibility"] == "conditional"
    assert envelope["reportGrade"] is False
    assert "AGGREGATION_POLICY_APPROVAL_REQUIRED" in envelope["warningCodes"]


def test_program_eligibility_blocks_legal_far_breach():
    result = evaluate_program_eligibility(
        applied_far=400,
        legal_far=300,
        constraint_snapshot_id="reg-1",
        constraint_approved=True,
    )

    assert result["eligibility"] == "ineligible"
    assert result["reportGrade"] is False
    assert result["blockingConstraintIds"] == ["LEGAL_FAR_EXCEEDED"]


def test_generic_execution_status_cannot_approve_regulatory_snapshot():
    project = _project(approved=False)
    project["regulatoryConstraintSnapshot"] = {
        "snapshotId": "reg-snapshot-unapproved",
        "status": "approved",
        "aggregationApproved": True,
    }

    envelope = build_preliminary_buildable_envelope(project, declared_land_area_sqm=3000)

    assert envelope["reportGrade"] is False
    assert envelope["aggregationApprovalStatus"] == "candidate"


def test_snapshot_hash_or_count_mismatch_never_becomes_report_grade():
    project = _project()
    project["regulatoryConstraintSnapshot"]["parcelSetHash"] = "d" * 64
    project["regulatoryConstraintSnapshot"]["parcelCount"] = 112

    envelope = build_preliminary_buildable_envelope(project, declared_land_area_sqm=3000)

    assert envelope["reportGrade"] is False
    failed = {check["name"] for check in envelope["snapshotBindingChecks"] if not check["passed"]}
    assert {"parcel_set_hash", "parcel_count"} <= failed
