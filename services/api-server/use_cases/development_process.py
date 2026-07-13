"""Structured real-estate development lifecycle used by the Chief Task Graph."""

from __future__ import annotations

from typing import Any, Dict


DEVELOPMENT_PROCESS_VERSION = "realytics-development-process-v1"


def get_development_process(asset_type: str | None = None) -> Dict[str, Any]:
    """Return the practical development lifecycle and its recalculation loops."""
    asset = (asset_type or "mixed_use").lower()
    return {
        "version": DEVELOPMENT_PROCESS_VERSION,
        "assetType": asset,
        "planningStages": [
            {"key": "direction_setting", "title": "개발방향 설정", "purpose": "기본현황·법규·입지·상권·수요·경쟁을 분석합니다.", "pods": ["land_legal", "market_product", "public_policy"], "requiredEvidence": ["site_identity", "zoning", "market_area", "demand_and_competition"], "outputs": ["development_thesis", "target_demand", "swot_risks"]},
            {"key": "development_concretization", "title": "개발구체화", "purpose": "용도·규모·Target·Anchor Tenant 또는 주요 수요층을 구체화합니다.", "pods": ["land_legal", "market_product", "cost_schedule"], "requiredEvidence": ["program", "product_type", "target_customer", "tenant_or_sales_strategy"], "outputs": ["development_program", "target_mix", "anchor_strategy"]},
            {"key": "design_and_product", "title": "설계 및 상품기획", "purpose": "공간계획·면적·세대/호실·주차·동선·상품성을 검토합니다.", "pods": ["land_legal", "market_product", "cost_schedule"], "requiredEvidence": ["basic_plan", "unit_mix", "exclusive_ratio", "parking", "gross_floor_area"], "outputs": ["approved_program_candidate", "cost_basis", "product_positioning"]},
            {"key": "sales_or_leasing", "title": "분양·임대 계획", "purpose": "분양가·임대료·계약률·흡수율·운영·마케팅 전략을 검토합니다.", "pods": ["market_product", "feasibility_cashflow", "pf_credit"], "requiredEvidence": ["pricing_basis", "rent_roll_or_presale", "absorption_assumption", "marketing_plan"], "outputs": ["sales_plan", "leasing_plan", "collection_schedule", "exit_assumption"]},
            {"key": "business_analysis", "title": "사업성 분석", "purpose": "수입·지출·금융비용·월별 Cash Flow·Max Minus·회수 가능성을 통합 분석합니다.", "pods": ["feasibility_cashflow", "pf_credit", "cost_schedule"], "requiredEvidence": ["revenue_items", "cost_items", "financing", "schedule"], "outputs": ["proforma", "monthly_cash_flow", "max_minus", "exit_sales_rate", "risk_cases"]},
        ],
        "executionStages": [
            {"key": "contractor_and_pf", "title": "시공사 선정 및 PF 유치", "pods": ["cost_schedule", "pf_credit", "land_legal"], "requiredEvidence": ["contractor_package", "responsible_completion", "pf_terms", "security_package"], "outputs": ["contractor_review", "pf_structure", "covenants", "conditions_precedent"]},
            {"key": "sales_execution", "title": "분양 단계", "pods": ["market_product", "feasibility_cashflow", "pf_credit"], "requiredEvidence": ["permit_or_sale_approval", "sales_notice", "contract_rate", "collection_actuals"], "outputs": ["actual_sales_rate", "collection_actuals", "discount_trigger_status"]},
            {"key": "leasing_execution", "title": "임대 단계", "pods": ["market_product", "feasibility_cashflow", "pf_credit"], "requiredEvidence": ["lease_contracts", "rent_roll", "occupancy", "tenant_credit"], "outputs": ["actual_noi", "occupancy", "lease_risk", "refinancing_capacity"]},
            {"key": "completion_and_operations", "title": "준공 및 운영 단계", "pods": ["land_legal", "cost_schedule", "feasibility_cashflow", "pf_credit"], "requiredEvidence": ["use_approval", "completion_record", "balance_collection", "management_contract"], "outputs": ["completion_status", "final_cost", "repayment_status", "operating_monitoring"]},
        ],
        "feedbackLoops": [
            {"event": "market_demand_changed", "from": "sales_or_leasing", "to": ["design_and_product", "business_analysis"], "rerun": ["market_product", "feasibility_cashflow"]},
            {"event": "design_program_changed", "from": "design_and_product", "to": ["development_concretization", "business_analysis"], "rerun": ["cost_schedule", "feasibility_cashflow", "pf_credit"]},
            {"event": "permit_or_zoning_constraint_changed", "from": "direction_setting", "to": ["development_concretization", "design_and_product"], "rerun": ["land_legal", "market_product", "feasibility_cashflow"]},
            {"event": "sales_or_lease_actuals_changed", "from": "sales_execution", "to": ["business_analysis"], "rerun": ["feasibility_cashflow", "pf_credit"]},
            {"event": "construction_cost_or_schedule_changed", "from": "contractor_and_pf", "to": ["business_analysis"], "rerun": ["cost_schedule", "feasibility_cashflow", "pf_credit"]},
            {"event": "operating_noi_changed", "from": "completion_and_operations", "to": ["business_analysis"], "rerun": ["market_product", "feasibility_cashflow", "pf_credit"]},
        ],
        "assetTypeMapping": {
            "residential": {"primaryOutput": "sales_plan", "requiredExtraEvidence": ["housing_sale_approval", "subscription_or_contract_rate"]},
            "commercial": {"primaryOutput": "sales_or_leasing_plan", "requiredExtraEvidence": ["foot_traffic", "rent_burden", "vacancy"]},
            "logistics": {"primaryOutput": "leasing_and_forward_sale", "requiredExtraEvidence": ["master_lease", "forward_sale", "cap_rate"]},
            "reit": {"primaryOutput": "distribution_capacity", "requiredExtraEvidence": ["appraisal", "noi", "distribution_policy"]},
            "fund": {"primaryOutput": "investor_recovery", "requiredExtraEvidence": ["fund_terms", "waterfall", "investor_reporting"]},
        },
    }
