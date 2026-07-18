from parsing_platform.domain_standards import (
    DOMAIN_STANDARDS,
    STANDARD_BUNDLES,
    RealEstateDomain,
    domain_standard_catalog,
    get_domain_standard,
)
from parsing_platform.handoff_policies import (
    APPROVED_POLICY_STATUS,
    DOMAIN_HANDOFF_POLICY_VERSION,
    NEEDS_DOMAIN_APPROVAL_STATUS,
    evaluate_handoff_completeness,
    get_domain_handoff_policy,
    missing_required_field_groups,
)


def test_domain_catalog_has_unique_owners_and_explicit_non_automatic_authority_policy():
    catalog = domain_standard_catalog()

    assert catalog["authorityPolicyStatus"] == "needs_domain_approval"
    assert catalog["autoSourceSelectionAllowed"] is False
    assert catalog["handoffPolicyVersion"] == DOMAIN_HANDOFF_POLICY_VERSION
    assert catalog["handoffPolicyStatus"] == "partially_approved"
    assert set(catalog["approvedHandoffPolicyDomains"]) == {
        "land_legal",
        "development_feasibility",
        "pf_credit",
    }
    approved = {
        item["domain"]: item
        for item in catalog["domains"]
        if item["handoff_policy_status"] == APPROVED_POLICY_STATUS
    }
    assert set(approved) == {
        "land_legal",
        "development_feasibility",
        "pf_credit",
    }
    assert all(
        item["handoff_policy_version"] == DOMAIN_HANDOFF_POLICY_VERSION
        for item in catalog["domains"]
    )
    assert all(
        item["handoff_policy_status"]
        in {APPROVED_POLICY_STATUS, NEEDS_DOMAIN_APPROVAL_STATUS}
        for item in catalog["domains"]
    )
    assert len({item.domain for item in DOMAIN_STANDARDS}) == len(DOMAIN_STANDARDS)
    assert all(item.owner_pod.endswith("_pod") for item in DOMAIN_STANDARDS)
    assert all(item.provided_capabilities for item in DOMAIN_STANDARDS)


def test_standard_bundles_keep_development_pf_and_income_asset_roles_composable():
    assert RealEstateDomain.DEVELOPMENT_FEASIBILITY in STANDARD_BUNDLES["development_core"]
    assert RealEstateDomain.PF_CREDIT in STANDARD_BUNDLES["pf_credit_core"]
    assert RealEstateDomain.SECURITY_COLLATERAL in STANDARD_BUNDLES["pf_credit_core"]
    assert RealEstateDomain.LEASE_OPERATIONS in STANDARD_BUNDLES["income_asset_core"]
    assert RealEstateDomain.VALUATION_EXIT in STANDARD_BUNDLES["income_asset_core"]

    pf = get_domain_standard("pf_credit")
    assert "candidate.financing" in pf.provided_capabilities
    assert "term_sheet" in pf.document_types

    feasibility = get_domain_standard("development_feasibility")
    assert "program." in feasibility.critical_field_prefixes
    assert feasibility.handoff_policy_status == APPROVED_POLICY_STATUS
    assert feasibility.required_approved_fields == (
        "program.gross_floor_area_sqm",
        "revenue.total_sales_revenue",
        "cost.total_project_cost",
    )

    pf = get_domain_standard("pf_credit")
    assert pf.required_approved_fields == ("financing.pf_amount",)

    land = get_domain_standard("land_legal")
    parcel_group = land.entity_scoped_required_field_groups[0]
    assert parcel_group.group_id == "parcel_core"
    assert parcel_group.cardinality == "all_expected"
    assert parcel_group.required_fields == (
        "land.pnu",
        "land.registered_area_sqm",
        "land.zoning.primary_use_district_code",
    )


def test_prefixes_are_scope_only_and_never_completeness_requirements():
    catalog = domain_standard_catalog()

    for item in catalog["domains"]:
        assert all(
            not field.endswith(".") and "*" not in field
            for field in item["required_approved_fields"]
        )
        assert all(
            not field.endswith(".") and "*" not in field
            for group in item["entity_scoped_required_field_groups"]
            for field in group["requiredFields"]
        )


def test_entity_count_uses_complete_same_entity_groups_not_aggregate_fields():
    policy = get_domain_handoff_policy("land_legal")
    split_across_entities = (
        {"fieldKey": "land.pnu", "entityId": "parcel-1"},
        {
            "fieldKey": "land.registered_area_sqm",
            "entityId": "parcel-1",
        },
        {
            "fieldKey": "land.zoning.primary_use_district_code",
            "entityId": "parcel-2",
        },
    )

    incomplete = evaluate_handoff_completeness(
        policy,
        split_across_entities,
        snapshot_metadata={"expectedEntityCount": 1},
    )

    assert incomplete.complete is False
    assert incomplete.entity_scoped_coverage[0].complete_entity_ids == ()
    assert dict(
        incomplete.entity_scoped_coverage[0].missing_fields_by_entity
    ) == {
        "parcel-1": ("land.zoning.primary_use_district_code",),
        "parcel-2": ("land.pnu", "land.registered_area_sqm"),
    }

    complete = evaluate_handoff_completeness(
        policy,
        (
            *split_across_entities[:2],
            {
                "fieldKey": "land.zoning.primary_use_district_code",
                "entityId": "parcel-1",
            },
        ),
        snapshot_metadata={"expectedEntityCount": 1},
    )
    assert complete.complete is True
    assert complete.entity_scoped_coverage[0].complete_entity_ids == (
        "parcel-1",
    )

    assert missing_required_field_groups(
        policy,
        (
            "land.pnu",
            "land.registered_area_sqm",
            "land.zoning.primary_use_district_code",
        ),
    ) == (
        (
            "land.pnu",
            "land.registered_area_sqm",
            "land.zoning.primary_use_district_code",
        ),
    )
