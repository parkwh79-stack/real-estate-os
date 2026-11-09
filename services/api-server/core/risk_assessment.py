from typing import List, Dict, Any, Optional

from core.schedule import schedule_event_map

RISK_CATEGORIES = [
    "Land Risk",
    "Permit Risk",
    "Developer Risk",
    "Contractor Risk",
    "Trust Risk",
    "Market Risk",
    "Sales Risk",
    "Construction Cost Risk",
    "Financing Risk",
    "Exit Risk",
    "Legal Risk",
    "Public Sector Risk"
]

def get_risk_grade(score: int) -> str:
    if 1 <= score <= 5:
        return "Low"
    elif 6 <= score <= 10:
        return "Moderate"
    elif 11 <= score <= 15:
        return "High"
    elif 16 <= score <= 25:
        return "Critical"
    return "Unknown"

def assess_risk_item(item_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluates a single risk item based on severity and probability.
    Required fields: category, item, severity (1-5), probability (1-5)
    Optional fields: description, mitigation, residual_risk, opinion
    """
    severity = item_data.get("severity", 1)
    probability = item_data.get("probability", 1)
    
    # Ensure bounds
    severity = max(1, min(5, severity))
    probability = max(1, min(5, probability))
    
    score = severity * probability
    grade = get_risk_grade(score)
    
    return {
        "category": item_data.get("category", "Unknown"),
        "item": item_data.get("item", "Unknown Risk"),
        "description": item_data.get("description", ""),
        "severity": severity,
        "probability": probability,
        "score": score,
        "grade": grade,
        "mitigation": item_data.get("mitigation", ""),
        "residual_risk": item_data.get("residual_risk", ""),
        "opinion": item_data.get("opinion", "")
    }

def calculate_overall_risk(assessed_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calculates overall project risk based on assessed items.
    """
    if not assessed_items:
        return {"overall_score": 0, "overall_grade": "Low", "critical_count": 0}
        
    total_score = sum(item.get("score", 0) for item in assessed_items)
    max_possible = len(assessed_items) * 25
    average_score = total_score / len(assessed_items)
    
    critical_items = [item for item in assessed_items if item.get("grade") == "Critical"]
    
    overall_grade = get_risk_grade(int(round(average_score)))
    
    # Elevate overall grade if there are critical risks
    if critical_items:
        overall_grade = "Critical"
        
    return {
        "average_score": average_score,
        "overall_grade": overall_grade,
        "critical_count": len(critical_items),
        "critical_items": [item.get("item") for item in critical_items]
    }

def build_basic_risk_matrix(
    project_data: Dict[str, Any],
    raw_items: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    items = [assess_risk_item(item) for item in (raw_items if raw_items is not None else project_data.get("risk_items", []))]
    if not items:
        items = _infer_default_risk_items(project_data)

    category_rows = []
    for category in RISK_CATEGORIES:
        category_items = [item for item in items if item.get("category") == category]
        if not category_items:
            category_rows.append({
                "category": category,
                "item_count": 0,
                "average_score": 0,
                "max_score": 0,
                "grade": "Not Assessed",
                "critical_count": 0,
                "top_item": "",
            })
            continue

        max_item = max(category_items, key=lambda item: item["score"])
        average_score = sum(item["score"] for item in category_items) / len(category_items)
        category_rows.append({
            "category": category,
            "item_count": len(category_items),
            "average_score": average_score,
            "max_score": max_item["score"],
            "grade": get_risk_grade(int(round(average_score))),
            "critical_count": len([item for item in category_items if item["grade"] == "Critical"]),
            "top_item": max_item["item"],
        })

    overall = calculate_overall_risk(items)
    top_risks = sorted(items, key=lambda item: item["score"], reverse=True)[:5]
    return {
        "matrixType": "basic_real_estate_pf_risk_matrix",
        "overall": overall,
        "categories": category_rows,
        "items": items,
        "topRisks": top_risks,
        "heatmap": _risk_heatmap(items),
        "recommendations": _risk_recommendations(overall, top_risks),
        "missingCategories": [row["category"] for row in category_rows if row["item_count"] == 0],
    }

def _infer_default_risk_items(project_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    feasibility = project_data.get("feasibility_input", {})
    regulation = project_data.get("regulation", {})
    schedule = schedule_event_map(project_data.get("schedule"))
    parcels = project_data.get("land_acquisition", [])
    pf_structure = project_data.get("pf_structure", [])
    covenants = project_data.get("covenants", {})

    total_revenue = float(feasibility.get("total_revenue", 0) or 0)
    total_cost = float(feasibility.get("total_cost", 0) or 0)
    total_pf = sum(float(tranche.get("amount", 0) or 0) for tranche in pf_structure)
    ltv = total_pf / total_revenue if total_revenue > 0 else 0
    ltc = total_pf / total_cost if total_cost > 0 else 0

    land_share = sum(float(parcel.get("share", 0) or 0) for parcel in parcels)
    land_issue = bool(parcels and land_share < 0.999) or not parcels
    mortgage_issue = any(parcel.get("mortgage") for parcel in parcels)
    permit_missing = not schedule.get("permit_approval")
    covenant_missing = not covenants.get("drawdown_conditions") or not covenants.get("eod")

    return [
        assess_risk_item({
            "category": "Land Risk",
            "item": "Land ownership and encumbrance readiness",
            "severity": 5 if land_issue else 3 if mortgage_issue else 2,
            "probability": 4 if land_issue else 3 if mortgage_issue else 2,
            "description": "Checks ownership share and existing encumbrances.",
            "mitigation": "Confirm 100% land control and release or trust-transfer conditions.",
        }),
        assess_risk_item({
            "category": "Permit Risk",
            "item": "Permit schedule readiness",
            "severity": 4 if permit_missing else 2,
            "probability": 4 if permit_missing else 2,
            "description": "Checks whether permit approval timing is present.",
            "mitigation": "Attach permit consultant memo and approval milestone evidence.",
        }),
        assess_risk_item({
            "category": "Financing Risk",
            "item": "PF leverage",
            "severity": 5 if ltc > 0.9 else 4 if ltc > 0.8 else 2,
            "probability": 4 if ltv > 0.8 else 3 if ltv > 0.65 else 2,
            "description": f"LTV {ltv:.2%}, LTC {ltc:.2%}.",
            "mitigation": "Adjust equity, tranche sizing, covenants, or pricing.",
        }),
        assess_risk_item({
            "category": "Exit Risk",
            "item": "Exit sale rate and repayment reliance",
            "severity": 4 if ltv > 0.75 else 2,
            "probability": 4 if ltv > 0.75 else 2,
            "description": "Proxy check using PF amount against revenue.",
            "mitigation": "Run discounted sale and unsold inventory scenarios.",
        }),
        assess_risk_item({
            "category": "Legal Risk",
            "item": "Covenant and legal package completeness",
            "severity": 4 if covenant_missing else 2,
            "probability": 3 if covenant_missing else 2,
            "description": "Checks core covenant and EOD presence.",
            "mitigation": "Complete drawdown conditions, EOD, waterfall, and security package.",
        }),
    ]

def _risk_heatmap(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cells = []
    for severity in range(1, 6):
        for probability in range(1, 6):
            matching = [
                item for item in items
                if item.get("severity") == severity and item.get("probability") == probability
            ]
            cells.append({
                "severity": severity,
                "probability": probability,
                "count": len(matching),
                "score": severity * probability,
                "grade": get_risk_grade(severity * probability),
            })
    return cells

def _risk_recommendations(overall: Dict[str, Any], top_risks: List[Dict[str, Any]]) -> List[str]:
    recommendations = []
    if overall.get("overall_grade") in {"High", "Critical"}:
        recommendations.append("Hold approval until top risks have owner, mitigation, and closing condition.")
    for item in top_risks[:3]:
        mitigation = item.get("mitigation")
        if mitigation:
            recommendations.append(f"{item['category']} - {item['item']}: {mitigation}")
    return recommendations or ["Maintain standard monitoring and covenant checks."]
