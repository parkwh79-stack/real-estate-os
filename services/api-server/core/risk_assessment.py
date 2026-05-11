from typing import List, Dict, Any, Optional

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
