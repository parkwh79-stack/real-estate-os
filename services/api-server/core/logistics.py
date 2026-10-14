from typing import Dict, Any

def calculate_logistics_noi(leasable_area_py: float, rent_per_py: float, operating_expenses: float = 0.0) -> float:
    """NOi = 임대면적 * 평당 임대료 * 12 - 운영비"""
    annual_rent = leasable_area_py * rent_per_py * 12
    return annual_rent - operating_expenses

def calculate_exit_value(noi: float, cap_rate: float) -> float | None:
    """Exit Value = NOI / Cap Rate"""
    if cap_rate <= 0:
        return None
    return noi / cap_rate

def calculate_forward_sale_ltv(pf_amount: float, forward_sale_price: float) -> float | None:
    """Forward Sale LTV = PF 대출금 / 선매각금액"""
    if forward_sale_price <= 0:
        return None
    return pf_amount / forward_sale_price

def calculate_appraisal_ltv(pf_amount: float, appraisal_value: float) -> float | None:
    """Appraisal LTV = PF 대출금 / 감정가"""
    if appraisal_value <= 0:
        return None
    return pf_amount / appraisal_value

def calculate_tenant_stability_score(tenant_credit_rating: str, lease_term_months: int) -> int:
    """Basic scoring logic for tenant stability (1-100)"""
    score = 0
    rating_upper = tenant_credit_rating.upper()
    
    if "AAA" in rating_upper:
        score += 50
    elif "AA" in rating_upper:
        score += 40
    elif "A" in rating_upper:
        score += 30
    elif "BBB" in rating_upper:
        score += 20
    else:
        score += 10
        
    if lease_term_months >= 120: # 10 years
        score += 50
    elif lease_term_months >= 60: # 5 years
        score += 40
    elif lease_term_months >= 36: # 3 years
        score += 20
    else:
        score += 10
        
    return min(100, score)

def calculate_location_score(distance_to_ic_km: float, distance_to_port_km: float) -> int:
    """Basic scoring logic for logistics location (1-100)"""
    score = 100
    
    # Deduct points for being far from IC
    if distance_to_ic_km > 10:
        score -= 20
    elif distance_to_ic_km > 5:
        score -= 10
        
    # Deduct points for being far from Port (assuming it's a requirement, otherwise optional)
    if distance_to_port_km > 50:
        score -= 10
        
    return max(0, score)

def calculate_spec_adequacy_score(clear_height: float, floor_load: float, ramp_available: bool) -> int:
    """Basic scoring logic for warehouse specifications (1-100)"""
    score = 0
    
    if clear_height >= 10.0:
        score += 40
    elif clear_height >= 8.0:
        score += 20
        
    if floor_load >= 2.0: # 2 ton/sqm
        score += 30
    elif floor_load >= 1.5:
        score += 15
        
    if ramp_available:
        score += 30
        
    return min(100, score)

def assess_logistics_risks(project_data: Dict[str, Any]) -> list:
    """Generates logistics specific risk items"""
    risks = []
    
    contract_status = project_data.get("pre_sale_contract_status")
    if not contract_status:
        risks.append({"category": "Data Quality", "item": "선매입계약 상태 확인 필요",
                      "description": "Contract status was not supplied; execution is unverified.",
                      "severity": 3, "probability": None})
    elif contract_status != "signed":
        risks.append({
            "category": "Market Risk",
            "item": "선매입계약 미체결 리스크",
            "description": "Forward sale contract is not fully executed.",
            "severity": 4,
            "probability": 3
        })
        
    tenant_rating = project_data.get("tenant_credit_rating")
    if not tenant_rating:
        risks.append({"category": "Data Quality", "item": "임차인 신용등급 확인 필요",
                      "description": "Tenant credit rating was not supplied; credit quality is unverified.",
                      "severity": 3, "probability": None})
    elif "BBB" not in tenant_rating.upper() and "A" not in tenant_rating.upper():
         risks.append({
            "category": "Market Risk",
            "item": "임차인 신용도 리스크",
            "description": "Tenant credit rating is below investment grade.",
            "severity": 3,
            "probability": 3
        })
         
    return risks
