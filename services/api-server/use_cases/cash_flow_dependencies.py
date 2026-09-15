"""Project dependent engines from the exact CashFlowRunRequest, never a stale project."""
from copy import deepcopy
from hashlib import sha256
import json


def financial_input_hash(request: dict) -> str:
    return sha256(json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def dependent_inputs(request: dict, result: dict, parent_id: str) -> tuple[dict, dict]:
    source = deepcopy(request)
    digest = financial_input_hash(source)
    common = {
        "parentCalculationRunId": parent_id,
        "financialInputHash": digest,
        "baseCashFlowRequest": source,
        "inputEvidenceIds": source.get("inputEvidenceIds") or source.get("evidenceIds") or [],
        "assumptionIds": source.get("assumptionIds") or [],
        "sourceMetadata": {"sourceName": "CashFlowRunRequest", "sourceType": "calculation_input", **(source.get("sourceMetadata") or {})},
        "sourceLocator": {"parentCalculationRunId": parent_id, "financialInputHash": digest, "engineKey": "cash_flow_engine"},
        "pfStructure": [{**row, "priority": row.get("repaymentPriority") or row.get("priority") or index}
                        for index, row in enumerate(source.get("pfStructure") or [], 1)],
    }
    scenario = {**deepcopy(common), "productMix": [
        {**row, "totalRevenue": row.get("totalSalesRevenue", row.get("totalRevenue")),
         "type": row.get("type") or row.get("productName") or row.get("productId")}
        for row in source.get("productMix") or []
    ]}
    costs = (source.get("sourcesAndUses") or {}).get("cost") or {}
    summary = result["summary"]
    stress = {
        **deepcopy(common),
        "totalRevenue": summary["totalRevenue"],
        "totalCost": summary["totalProjectCost"],
        "landCost": costs.get("landCost", 0),
        "constructionCost": costs.get("directConstructionCost", 0),
        "financeCost": costs.get("financialCost", 0),
        "otherCost": costs.get("otherCost", 0),
        "pfAmount": summary["pfLimit"],
        "schedule": {"months": max(1, len(result["monthlyCashFlow"]) - 1),
                     "salesEndMonth": max(1, len(result["monthlyCashFlow"]) - 1)},
    }
    return scenario, stress


def scale_cash_flow_request(base: dict, *, revenue: float, construction: float, finance: float) -> dict:
    """Apply stress amounts while preserving actual collection and cost timing.

    A nonzero amount cannot be allocated to a source schedule with a zero base;
    such scenarios require a reviewed allocation instead of inventing dates.
    """
    request = deepcopy(base)
    amounts = request["sourcesAndUses"]
    def factor(before, after):
        before = float(before or 0)
        if before == 0:
            if after != 0:
                raise ValueError("stress_allocation_required_for_zero_base")
            return 1.0
        return after / before
    revenue_factor = factor(amounts["revenue"].get("totalSalesRevenue"), revenue)
    construction_factor = factor(amounts["cost"].get("directConstructionCost"), construction)
    finance_factor = factor(amounts["cost"].get("financialCost"), finance)
    amounts["revenue"]["totalSalesRevenue"] = revenue
    amounts["cost"]["directConstructionCost"] = construction
    amounts["cost"]["financialCost"] = finance
    amounts["cost"]["totalProjectCost"] = sum(float(amounts["cost"].get(key) or 0) for key in ("landCost", "directConstructionCost", "financialCost", "otherCost"))
    for product in request.get("productMix") or []:
        product["totalSalesRevenue"] = float(product.get("totalSalesRevenue") or 0) * revenue_factor
        for row in [product, *(product.get("collectionSteps") or [])]:
            if isinstance(row.get("monthlyCollectionAmounts"), list):
                row["monthlyCollectionAmounts"] = [float(value or 0) * revenue_factor for value in row["monthlyCollectionAmounts"]]
    for row in (request.get("costSchedule") or {}).get("rows") or []:
        for key, multiplier in (("constructionCost", construction_factor), ("financialCost", finance_factor)):
            if key in row:
                row[key] = float(row[key] or 0) * multiplier
    # Stress projections never inherit a human approval for their changed values.
    from use_cases.cash_flow import _downgrade_untrusted_approval_claims
    _downgrade_untrusted_approval_claims(request)
    request["authorityStatus"] = "scenario_candidate"
    request["reportEligible"] = False
    return request
