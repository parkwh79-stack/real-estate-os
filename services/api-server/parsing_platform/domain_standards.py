from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


DOMAIN_STANDARD_VERSION = "realytics-real-estate-domain-standard-v1"


class RealEstateDomain(str, Enum):
    PROJECT_IDENTITY = "project_identity"
    LAND_LEGAL = "land_legal"
    PERMIT_PLANNING = "permit_planning"
    BUILDING_PRODUCT = "building_product"
    MARKET_PRODUCT = "market_product"
    DEVELOPMENT_FEASIBILITY = "development_feasibility"
    COST_SCHEDULE = "cost_schedule"
    PF_CREDIT = "pf_credit"
    SECURITY_COLLATERAL = "security_collateral"
    CONTRACT_OBLIGATION = "contract_obligation"
    LEASE_OPERATIONS = "lease_operations"
    VALUATION_EXIT = "valuation_exit"
    CASHFLOW_WATERFALL = "cashflow_waterfall"
    RISK_APPROVAL = "risk_approval"


@dataclass(frozen=True)
class DomainStandard:
    domain: RealEstateDomain
    owner_pod: str
    required_capabilities: tuple[str, ...]
    provided_capabilities: tuple[str, ...]
    document_types: tuple[str, ...]
    critical_field_prefixes: tuple[str, ...]
    downstream_pods: tuple[str, ...]
    handoff_policy_status: str = "draft_not_approved"
    required_approved_field_groups: tuple[tuple[str, ...], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["domain"] = self.domain.value
        payload["required_capabilities"] = list(self.required_capabilities)
        payload["provided_capabilities"] = list(self.provided_capabilities)
        payload["document_types"] = list(self.document_types)
        payload["critical_field_prefixes"] = list(self.critical_field_prefixes)
        payload["downstream_pods"] = list(self.downstream_pods)
        payload["required_approved_field_groups"] = [
            list(group) for group in self.required_approved_field_groups
        ]
        return payload


DOMAIN_STANDARDS: tuple[DomainStandard, ...] = (
    DomainStandard(
        RealEstateDomain.PROJECT_IDENTITY,
        "project_identity_pod",
        ("document.classified", "document.structure"),
        ("candidate.project_identity",),
        ("business_plan", "investment_memorandum", "credit_review_memo"),
        ("project.", "deal."),
        ("land_legal_pod", "development_feasibility_pod", "pf_credit_pod"),
    ),
    DomainStandard(
        RealEstateDomain.LAND_LEGAL,
        "land_legal_pod",
        ("document.structure",),
        ("candidate.land", "candidate.ownership", "candidate.encumbrance"),
        ("land_ledger", "registry", "land_sale_agreement", "land_use_plan"),
        ("land.", "parcel.", "ownership.", "encumbrance."),
        ("permit_planning_pod", "development_feasibility_pod", "pf_credit_pod"),
    ),
    DomainStandard(
        RealEstateDomain.PERMIT_PLANNING,
        "permit_planning_pod",
        ("candidate.land",),
        ("candidate.permit", "candidate.zoning"),
        ("permit", "land_use_plan", "architectural_plan"),
        ("permit.", "zoning.", "planning."),
        ("building_product_pod", "cost_schedule_pod", "pf_credit_pod"),
    ),
    DomainStandard(
        RealEstateDomain.BUILDING_PRODUCT,
        "building_product_pod",
        ("candidate.zoning", "document.structure"),
        ("candidate.building_program", "candidate.product_mix"),
        ("architectural_plan", "business_plan", "sales_plan"),
        ("building.", "product_mix."),
        ("market_product_pod", "development_feasibility_pod", "cost_schedule_pod"),
    ),
    DomainStandard(
        RealEstateDomain.MARKET_PRODUCT,
        "market_product_pod",
        ("candidate.product_mix",),
        ("candidate.market", "candidate.sales_assumption"),
        ("market_report", "transaction_comparables", "presale_comparables", "appraisal_report"),
        ("market.", "market_comparable.", "sales_assumption."),
        ("development_feasibility_pod", "valuation_exit_pod", "pf_credit_pod"),
    ),
    DomainStandard(
        RealEstateDomain.DEVELOPMENT_FEASIBILITY,
        "development_feasibility_pod",
        ("candidate.product_mix", "candidate.market"),
        ("candidate.revenue", "candidate.cost", "candidate.feasibility_input"),
        ("feasibility_workbook", "business_plan", "budget"),
        ("feasibility.", "revenue.", "cost."),
        ("cashflow_waterfall_pod", "pf_credit_pod", "risk_approval_pod"),
    ),
    DomainStandard(
        RealEstateDomain.COST_SCHEDULE,
        "cost_schedule_pod",
        ("candidate.building_program",),
        ("candidate.cost", "candidate.schedule"),
        ("construction_contract", "budget", "progress_report", "schedule"),
        ("cost.", "schedule.", "construction."),
        ("development_feasibility_pod", "cashflow_waterfall_pod", "pf_credit_pod"),
    ),
    DomainStandard(
        RealEstateDomain.PF_CREDIT,
        "pf_credit_pod",
        ("candidate.feasibility_input", "candidate.schedule"),
        ("candidate.financing", "candidate.covenant", "candidate.credit_term"),
        ("term_sheet", "loan_agreement", "credit_review_memo", "financial_statement"),
        ("financing.", "pf.", "covenant.", "credit."),
        ("security_collateral_pod", "cashflow_waterfall_pod", "risk_approval_pod"),
    ),
    DomainStandard(
        RealEstateDomain.SECURITY_COLLATERAL,
        "security_collateral_pod",
        ("candidate.financing", "candidate.encumbrance"),
        ("candidate.security", "candidate.credit_enhancement"),
        ("security_agreement", "trust_agreement", "guarantee", "registry"),
        ("security.", "collateral.", "credit_enhancement."),
        ("pf_credit_pod", "risk_approval_pod"),
    ),
    DomainStandard(
        RealEstateDomain.CONTRACT_OBLIGATION,
        "contract_obligation_pod",
        ("document.structure",),
        ("candidate.obligation", "candidate.condition", "candidate.event_of_default"),
        ("loan_agreement", "construction_contract", "trust_agreement", "lease_agreement"),
        ("obligation.", "condition.", "event_of_default."),
        ("pf_credit_pod", "risk_approval_pod"),
    ),
    DomainStandard(
        RealEstateDomain.LEASE_OPERATIONS,
        "lease_operations_pod",
        ("document.structure",),
        ("candidate.lease", "candidate.operating_income"),
        ("lease_agreement", "rent_roll", "operating_statement"),
        ("lease.", "tenant.", "operating_income."),
        ("valuation_exit_pod", "cashflow_waterfall_pod", "pf_credit_pod"),
    ),
    DomainStandard(
        RealEstateDomain.VALUATION_EXIT,
        "valuation_exit_pod",
        ("candidate.market",),
        ("candidate.valuation", "candidate.exit"),
        ("appraisal_report", "market_report", "investment_memorandum"),
        ("valuation.", "exit."),
        ("pf_credit_pod", "risk_approval_pod"),
    ),
    DomainStandard(
        RealEstateDomain.CASHFLOW_WATERFALL,
        "cashflow_waterfall_pod",
        ("candidate.revenue", "candidate.cost", "candidate.financing"),
        ("candidate.cashflow_input", "candidate.waterfall_rule"),
        ("feasibility_workbook", "cashflow_workbook", "loan_agreement", "account_agreement"),
        ("cash_flow.", "waterfall.", "project_account."),
        ("pf_credit_pod", "risk_approval_pod"),
    ),
    DomainStandard(
        RealEstateDomain.RISK_APPROVAL,
        "risk_approval_pod",
        ("candidate.reconciled",),
        ("review.queue", "approval.snapshot_candidate"),
        ("credit_review_memo", "investment_committee_memo"),
        ("risk.", "approval_condition."),
        (),
    ),
)


# NEEDS-DOMAIN-DECISION: These policies describe comparison context only.  They
# are intentionally not executable auto-selection rankings until Legal, Credit,
# Development, Data Architecture, and QA owners approve them field by field.
DRAFT_SOURCE_AUTHORITY_CONTEXT: dict[RealEstateDomain, tuple[str, ...]] = {
    RealEstateDomain.LAND_LEGAL: ("official_registry", "executed_contract", "submitted_document"),
    RealEstateDomain.PERMIT_PLANNING: ("final_permit", "official_registry", "submitted_document"),
    RealEstateDomain.DEVELOPMENT_FEASIBILITY: ("approved_scenario", "submitted_document", "draft_document"),
    RealEstateDomain.PF_CREDIT: ("executed_contract", "signed_report", "submitted_document"),
    RealEstateDomain.SECURITY_COLLATERAL: ("official_registry", "executed_contract", "submitted_document"),
    RealEstateDomain.MARKET_PRODUCT: ("external_market_data", "signed_report", "submitted_document"),
    RealEstateDomain.VALUATION_EXIT: ("signed_report", "external_market_data", "submitted_document"),
}


STANDARD_BUNDLES: dict[str, tuple[RealEstateDomain, ...]] = {
    "development_core": (
        RealEstateDomain.PROJECT_IDENTITY,
        RealEstateDomain.LAND_LEGAL,
        RealEstateDomain.PERMIT_PLANNING,
        RealEstateDomain.BUILDING_PRODUCT,
        RealEstateDomain.MARKET_PRODUCT,
        RealEstateDomain.DEVELOPMENT_FEASIBILITY,
        RealEstateDomain.COST_SCHEDULE,
    ),
    "pf_credit_core": (
        RealEstateDomain.PROJECT_IDENTITY,
        RealEstateDomain.LAND_LEGAL,
        RealEstateDomain.DEVELOPMENT_FEASIBILITY,
        RealEstateDomain.COST_SCHEDULE,
        RealEstateDomain.PF_CREDIT,
        RealEstateDomain.SECURITY_COLLATERAL,
        RealEstateDomain.CONTRACT_OBLIGATION,
        RealEstateDomain.CASHFLOW_WATERFALL,
        RealEstateDomain.RISK_APPROVAL,
    ),
    "income_asset_core": (
        RealEstateDomain.PROJECT_IDENTITY,
        RealEstateDomain.LAND_LEGAL,
        RealEstateDomain.MARKET_PRODUCT,
        RealEstateDomain.LEASE_OPERATIONS,
        RealEstateDomain.VALUATION_EXIT,
        RealEstateDomain.PF_CREDIT,
        RealEstateDomain.CASHFLOW_WATERFALL,
        RealEstateDomain.RISK_APPROVAL,
    ),
}


def domain_standard_catalog() -> dict[str, Any]:
    return {
        "schemaVersion": DOMAIN_STANDARD_VERSION,
        "catalogStatus": "internal_controlled_baseline",
        "normativeIndustryStandardCompliance": False,
        "executableBundles": False,
        "authorityPolicyStatus": "needs_domain_approval",
        "autoSourceSelectionAllowed": False,
        "knownCompositionGaps": [
            "Domain bundles are taxonomy/grouping aids until dependency closure and provider bindings are approved.",
            "candidate.reconciled has no production reconciliation provider in this foundation.",
        ],
        "domains": [item.to_dict() for item in DOMAIN_STANDARDS],
        "bundles": {
            key: [domain.value for domain in domains]
            for key, domains in STANDARD_BUNDLES.items()
        },
        "draftAuthorityContext": {
            key.value: list(values) for key, values in DRAFT_SOURCE_AUTHORITY_CONTEXT.items()
        },
    }


def get_domain_standard(domain: RealEstateDomain | str) -> DomainStandard:
    resolved = domain if isinstance(domain, RealEstateDomain) else RealEstateDomain(domain)
    return next(item for item in DOMAIN_STANDARDS if item.domain == resolved)
