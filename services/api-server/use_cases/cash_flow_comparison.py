"""Read-only comparison of persisted, completed monthly cash-flow runs."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
import math
from typing import Any

from core.calculations import calculate_development_profit
from core.financial_input_validation import FinancialInputError
from core.schedule import schedule_event_map, schedule_month_indices
from use_cases.cash_flow_dependencies import financial_input_hash


SELECTION_KEY = "cashFlowComparison"
_EXECUTION_KEYS = frozenset({
    "calculationRunId", "parentCalculationRunId", "runId", "requestId", "traceId",
    "scenarioSetId", "createdAt", "updatedAt", "generatedAt", "collectedAt",
    "created_at", "updated_at", "generated_at", "collected_at", "financialInputHash",
    "sourceMetadata", "sourceLocator", "sourceLocators", "provenance", "inputDigest",
    "inputEvidenceIds", "evidenceIds", "assumptionIds", "artifactRef",
    "reviewedMonthlyFinancingReview",
})


def content_hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _snapshot(run: dict, key: str) -> dict:
    value = run.get(key)
    return value if isinstance(value, dict) else {}


def _valid_json(value: Any) -> bool:
    try:
        content_hash(value)
    except (TypeError, ValueError):
        return False
    return True


def business_input(value: Any) -> Any:
    """Ignore execution/provenance metadata only in the display diff, never in authority hashes."""
    if isinstance(value, dict):
        return {key: business_input(item) for key, item in value.items() if key not in _EXECUTION_KEYS}
    if isinstance(value, list):
        return [business_input(item) for item in value]
    return value


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _source_hashes(value: Any) -> list[str]:
    hashes: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"financialInputHash", "inputHash", "businessInputHash"}:
                continue
            if isinstance(item, str) and (key.lower().endswith("hash") or key.lower() == "sha256"):
                hashes.add(item)
            else:
                hashes.update(_source_hashes(item))
    elif isinstance(value, list):
        for item in value:
            hashes.update(_source_hashes(item))
    return sorted(hashes)


def _period(run: dict) -> dict:
    snapshot = _snapshot(run, "inputSnapshot")
    result = _snapshot(run, "resultSnapshot")
    rows = result.get("monthlyCashFlow")
    indices = [row.get("monthIndex") for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    valid = bool(indices) and len(indices) == len(rows) and all(
        isinstance(index, int) and not isinstance(index, bool) and index == offset
        for offset, index in enumerate(indices)
    )
    base_month = None
    try:
        schedule_indices = schedule_month_indices(snapshot.get("schedule"))
        for key, value in schedule_event_map(snapshot.get("schedule")).items():
            if key not in schedule_indices or not isinstance(value, str) or len(value) not in (7, 10):
                continue
            try:
                parsed = date.fromisoformat(value if len(value) == 10 else value + "-01")
            except ValueError:
                continue
            year, month = divmod(parsed.year * 12 + parsed.month - 1 - schedule_indices[key], 12)
            base_month = f"{year:04d}-{month + 1:02d}"
            break
    except FinancialInputError:
        valid = False
    return {"basis": "monthly", "axis": "calendar" if base_month else "relative", "baseMonth": base_month, "firstMonthIndex": 0 if valid else None,
            "lastMonthIndex": indices[-1] if valid else None, "rowCount": len(indices), "valid": valid}


def run_summary(run: dict) -> dict:
    snapshot = _snapshot(run, "inputSnapshot")
    result = _snapshot(run, "resultSnapshot")
    snapshot_valid, result_valid = _valid_json(snapshot), _valid_json(result)
    financing = result.get("monthlyFinancing")
    policy = snapshot.get("monthlyFinancing") or (financing.get("policy") if isinstance(financing, dict) else {}) or {}
    source_metadata = snapshot.get("sourceMetadata") or result.get("sourceMetadata") or {}
    scenario = run.get("scenario")
    return {
        "calculationRunId": run.get("calculationRunId"),
        "label": (scenario.get("name") if isinstance(scenario, dict) else None) or run.get("createdAt") or run.get("calculationRunId"),
        "createdAt": run.get("createdAt"), "engineKey": run.get("engineKey"),
        "engineVersion": run.get("engineVersion"), "status": run.get("status"),
        "dataStatus": result.get("dataStatus"),
        "currency": snapshot.get("currency", "KRW"), "unit": snapshot.get("unit", "KRW"),
        "period": _period(run), "policyMode": policy.get("method", "budget_finance_allocation") if isinstance(policy, dict) else None,
        "financialInputHash": run.get("financialInputHash") or snapshot.get("financialInputHash"),
        "inputHash": content_hash(snapshot) if snapshot_valid else None, "resultHash": content_hash(result) if result_valid else None,
        "businessInputHash": content_hash(business_input(snapshot)) if snapshot_valid else None,
        "sourceHashes": _source_hashes(snapshot),
        "sourceMetadata": deepcopy(source_metadata) if _valid_json(source_metadata) else {},
        "sourceLocator": deepcopy(run.get("sourceLocator") or {}),
        "inputEvidenceIds": deepcopy(run.get("inputEvidenceIds") or []),
        "assumptionIds": deepcopy(run.get("assumptionIds") or []),
    }


def run_issues(run: dict) -> list[str]:
    issues = []
    if run.get("engineKey") != "cash_flow_engine":
        issues.append("unsupported_engine")
    result = run.get("resultSnapshot")
    snapshot = run.get("inputSnapshot")
    if not isinstance(result, dict) or not isinstance(snapshot, dict) or not snapshot:
        return issues + ["calculation_snapshot_missing"]
    if not _valid_json(snapshot) or not _valid_json(result):
        return issues + ["invalid_calculation_values"]
    if run.get("status") != "completed" or result.get("status") != "completed":
        issues.append("calculation_not_completed")
    if result.get("analysisType") != "realytics_cash_flow_engine_v1":
        issues.append("unsupported_result_type")
    if snapshot.get("currency", "KRW") != "KRW" or snapshot.get("unit", "KRW") != "KRW":
        issues.append("unsupported_currency_or_unit")
    if not _period(run)["valid"]:
        issues.append("invalid_month_indices")
    if not isinstance(result.get("summary"), dict):
        issues.append("calculation_summary_missing")
    elif any(_number(result["summary"].get(key)) is None for key in ("totalRevenue", "totalProjectCost", "totalPfDraw", "endingCash")):
        issues.append("calculation_summary_missing")
    digest = run.get("financialInputHash") or snapshot.get("financialInputHash")
    if digest:
        source = {key: value for key, value in snapshot.items() if key != "financialInputHash"}
        if (snapshot.get("financialInputHash") not in (None, digest)
                or digest != financial_input_hash(source)):
            issues.append("financial_input_hash_mismatch")
    return issues


def comparison_state_hash(project: dict, records: list[dict]) -> str:
    # Reject corrupt authority instead of exposing non-JSON numbers or inventing a fingerprint.
    return content_hash({"project": project, "runs": [
        {"id": row.get("calculationRunId"), "hash": content_hash(row)}
        for row in sorted(records, key=lambda item: str(item.get("calculationRunId")))
    ]})


def _changes(before: Any, after: Any, path: str = "") -> list[dict]:
    if (type(before) is type(after) or (_number(before) is not None and _number(after) is not None)) and before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        rows = []
        for key in sorted(set(before) | set(after)):
            child = f"{path}.{key}" if path else key
            if key not in before or key not in after:
                rows.append({"path": child, "before": before.get(key), "after": after.get(key),
                             "kind": "added" if key not in before else "removed"})
            else:
                rows.extend(_changes(before[key], after[key], child))
        return rows
    if isinstance(before, list) and isinstance(after, list):
        rows = []
        for index in range(max(len(before), len(after))):
            if index >= len(before) or index >= len(after):
                rows.append({"path": f"{path}[{index}]", "before": before[index] if index < len(before) else None,
                             "after": after[index] if index < len(after) else None,
                             "kind": "added" if index >= len(before) else "removed"})
            else:
                rows.extend(_changes(before[index], after[index], f"{path}[{index}]"))
        return rows
    return [{"path": path, "before": before, "after": after, "kind": "changed"}]


def _change_label(path: str) -> str:
    key = path.lower()
    if key.startswith("monthlyfinancing"):
        return "금융비용 계산방식"
    if "schedule" in key or "monthindex" in key or "month_index" in key:
        return "사업 일정"
    if "interest" in key or "coupon" in key:
        return "대출 금리"
    if "maturity" in key or "tenor" in key:
        return "대출 기간"
    if "tranche" in key or "funding" in key or "facility" in key or "loan" in key:
        return "자금조달 조건"
    if "financialcost" in key:
        return "금융비용 예산"
    if "cost" in key:
        return "사업비"
    if "collection" in key or "payment" in key:
        return "분양대금 수납 조건"
    if "revenue" in key or "productmix" in key or "sale" in key:
        return "분양·매출 계획"
    if "waterfall" in key:
        return "자금 배분 조건"
    if key in {"currency", "unit"}:
        return "금액 단위"
    return "사업성 입력"


def _source_identity(run: dict) -> dict:
    snapshot = _snapshot(run, "inputSnapshot")
    result = _snapshot(run, "resultSnapshot")
    return {
        "hashes": _source_hashes(snapshot),
        "metadata": business_input(snapshot.get("sourceMetadata") or result.get("sourceMetadata") or {}),
        "locator": business_input(run.get("sourceLocator") or snapshot.get("sourceLocator") or {}),
        "inputEvidenceIds": run.get("inputEvidenceIds") or [],
        "assumptionIds": run.get("assumptionIds") or [],
    }


def _metrics(run: dict) -> dict:
    result = run["resultSnapshot"]
    summary = result.get("summary") or {}
    reconciliation = (result.get("monthlyFinancing") or {}).get("reconciliation") or {}
    values = {key: _number(summary.get(key)) for key in (
        "totalRevenue", "totalProjectCost", "developmentProfit", "financialCost", "requiredPfLimit",
        "totalPfDraw", "endingCash", "outstandingDebt",
    )}
    derived = []
    if values["developmentProfit"] is None and values["totalRevenue"] is not None and values["totalProjectCost"] is not None:
        values["developmentProfit"] = calculate_development_profit(values["totalRevenue"], values["totalProjectCost"])
        derived.append("developmentProfit")
    if values["requiredPfLimit"] is None and _number(summary.get("maxMinus")) is not None:
        values["requiredPfLimit"] = abs(summary["maxMinus"])
        derived.append("requiredPfLimit")
    if values["financialCost"] is None:
        values["financialCost"] = _number(reconciliation.get("calculatedFinancialCost"))
    if values["financialCost"] is None:
        amounts = [_number((row.get("cashOut") or {}).get("financialCost")) for row in result["monthlyCashFlow"]]
        if all(value is not None for value in amounts):
            values["financialCost"] = sum(amounts)
            derived.append("financialCost")
    if values["outstandingDebt"] is None:
        values["outstandingDebt"] = _number(reconciliation.get("outstandingDebt"))
    return {"values": values, "derived": derived}


def compare_cash_flow_runs(baseline: dict, changed: dict, *, saved_selection: dict | None = None) -> dict:
    reasons = sorted(set(run_issues(baseline) + run_issues(changed)))
    base, other = run_summary(baseline), run_summary(changed)
    if baseline.get("engineVersion") != changed.get("engineVersion"):
        reasons.append("engine_version_mismatch")
    if baseline.get("calculationRunId") == changed.get("calculationRunId"):
        reasons.append("same_calculation_run")
    if saved_selection:
        for side, info in (("baseline", base), ("comparison", other)):
            for field in ("InputHash", "ResultHash"):
                expected = saved_selection.get(side + field)
                if expected and expected != info[field[0].lower() + field[1:]]:
                    reasons.append("saved_calculation_changed")
    response = {"status": "incompatible" if reasons else "ready", "reasons": sorted(set(reasons)),
                "baseline": base, "comparison": other, "metrics": [], "inputChanges": [],
                "sourceChanged": _source_identity(baseline) != _source_identity(changed),
                "businessInputsChanged": business_input(baseline.get("inputSnapshot")) != business_input(changed.get("inputSnapshot")),
                "inputChangeCount": 0, "inputChangesTruncated": False,
                "reportEligible": False, "approvalStatus": "not_requested"}
    if reasons:
        return response
    if base["period"] != other["period"]:
        response["status"] = "partial"
        response["reasons"].append("period_mismatch")
    if base["policyMode"] != other["policyMode"]:
        response["status"] = "partial"
        response["reasons"].append("financing_policy_mismatch")
    left, right = _metrics(baseline), _metrics(changed)
    labels = {"totalRevenue": "총매출", "totalProjectCost": "총사업비", "developmentProfit": "개발이익",
              "financialCost": "금융비용", "requiredPfLimit": "최대 부족자금", "totalPfDraw": "총 대출 인출",
              "endingCash": "기말 현금", "outstandingDebt": "잔여 대출"}
    for key, label in labels.items():
        before, after = left["values"][key], right["values"][key]
        response["metrics"].append({"key": key, "label": label, "unit": "KRW",
            "baselineValue": before, "comparisonValue": after,
            "delta": after - before if before is not None and after is not None else None,
            "status": "available" if before is not None and after is not None else "unavailable",
            "baselineDerived": key in left["derived"], "comparisonDerived": key in right["derived"]})
    changes = _changes(business_input(baseline["inputSnapshot"]), business_input(changed["inputSnapshot"]))
    for item in changes:
        item["label"] = _change_label(item["path"])
    response.update(inputChanges=changes[:1000], inputChangeCount=len(changes), inputChangesTruncated=len(changes) > 1000)
    return response
