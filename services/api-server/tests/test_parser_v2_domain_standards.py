from parsing_platform.domain_standards import (
    DOMAIN_STANDARDS,
    STANDARD_BUNDLES,
    RealEstateDomain,
    domain_standard_catalog,
    get_domain_standard,
)


def test_domain_catalog_has_unique_owners_and_explicit_non_automatic_authority_policy():
    catalog = domain_standard_catalog()

    assert catalog["authorityPolicyStatus"] == "needs_domain_approval"
    assert catalog["autoSourceSelectionAllowed"] is False
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
