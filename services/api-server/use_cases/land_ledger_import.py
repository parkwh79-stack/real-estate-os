from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict

from agents.land_ledger_agent import LAND_LEDGER_AGENT_VERSION, LandLedgerAgent


def analyze_land_ledger_workbook_use_case(
    file_path: str,
    import_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """Analyze an uploaded land ledger workbook from a prepared local file path."""
    return LandLedgerAgent().analyze_xls(file_path, import_metadata)


def enrich_land_ledger_use_case(
    land_ledger: Dict[str, Any],
    enrichment_options: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run batch PNU, cadastral, official-price, and registry enrichment on a parsed ledger."""
    return LandLedgerAgent().enrich_analysis(land_ledger, enrichment_options or {})


def reconcile_land_costs_use_case(project_data: Dict[str, Any]) -> Dict[str, Any]:
    """Compare persisted source scopes for review without changing inputs.

    A candidate is useful evidence for this comparison even when its workbook
    has errors. This result never approves it or feeds financial calculations.
    """
    ledger = project_data.get("landLedger") or project_data.get("land_ledger") or {}
    study_key = next((key for key in (
        "feasibilityStudyV2Draft", "canonicalFeasibilityStudyV2", "feasibilityStudyV2",
    ) if isinstance(project_data.get(key), dict) and project_data[key]), None)
    study = project_data.get(study_key, {}) if study_key else {}
    raw_import = study.get("raw_import") or {}
    breakdown = raw_import.get("landCostBreakdown") or {}
    summary = ledger.get("summary") or {}
    totals = ledger.get("sourceTotals") or {}

    def amount(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        return value

    groups = {group["contractGroupId"] for group in ledger.get("contractGroups", [])
              if group.get("contractGroupId") and amount(group.get("totalContractAmount")) is not None}
    contract_complete = bool(ledger.get("parcels")) and all(
        amount(parcel.get("totalContractAmount")) is not None or parcel.get("contractGroupId") in groups
        for parcel in ledger.get("parcels", [])
    )
    contract = amount(summary.get("totalContractAmount")) if contract_complete else None
    purchase = amount((breakdown.get("purchaseBudget") or {}).get("value"))
    inclusive = amount((breakdown.get("inclusiveBudget") or {}).get("value"))
    difference = purchase - contract if purchase is not None and contract is not None else None
    ancillary = inclusive - purchase if inclusive is not None and purchase is not None else None
    source_row = totals.get("row")
    parcel_rows = [parcel["source"]["row"] for parcel in ledger.get("parcels", [])
                   if isinstance((parcel.get("source") or {}).get("row"), int)]
    reimport_required = bool(ledger.get("parcels")) and ledger.get("parserVersion") != LAND_LEDGER_AGENT_VERSION
    result = {
        "schemaVersion": "land-cost-source-reconciliation-v1",
        "scope": "source_comparison_only",
        "status": "incomplete" if any(value is None for value in (contract, purchase, inclusive))
        else "needs_review" if difference != 0 or reimport_required or (ledger.get("importConsistency") or {}).get("status") != "passed"
        else "amounts_match",
        "studyStatus": "candidate" if study_key == "feasibilityStudyV2Draft" else "saved_source" if study_key else "missing",
        "workbookValidationStatus": raw_import.get("validationStatus"),
        "requiresLedgerReimport": reimport_required,
        "ledgerContract": {
            "value": contract, "unit": "KRW",
            "sourceLocator": {"sourceType": "derived_excel_sum", "sourceName": ledger.get("sourceFile"),
                              "sheet": ledger.get("sheetName"),
                              "range": f"I{min(parcel_rows)}:I{max(parcel_rows)}" if parcel_rows else None},
            "sourceTotal": totals.get("totalContractAmount"),
            "sourceTotalLocator": {"sheet": ledger.get("sheetName"), "cell": f"I{source_row}" if source_row else None},
        },
        "purchaseBudget": breakdown.get("purchaseBudget"),
        "inclusiveBudget": breakdown.get("inclusiveBudget"),
        "ancillaryBudgetKrw": ancillary,
        "purchaseMinusContractKrw": difference,
        "ledgerConsistencyStatus": (ledger.get("importConsistency") or {}).get("status"),
        "individualAmountMissingCount": summary.get("individualAmountMissingCount"),
        "contractGroupCount": summary.get("contractGroupCount"),
        "sourceHashes": {
            "ledger": (ledger.get("importMetadata") or {}).get("originalFileHash"),
            "workbook": next(((raw_import.get("sourceDocument") or {}).get(key)
                              for key in ("sha256", "originalFileHash", "archiveId")
                              if (raw_import.get("sourceDocument") or {}).get(key)), None),
        },
    }
    basis = {"projectId": (project_data.get("project") or {}).get("id") or project_data.get("id"),
             "comparison": result, "contractGroups": ledger.get("contractGroups"),
             "parcelSources": [parcel.get("source") for parcel in ledger.get("parcels", [])]}
    result["comparisonHash"] = hashlib.sha256(json.dumps(basis, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
    review = project_data.get("landCostReview") or {}
    result["review"] = review
    result["reviewMatchesCurrentSources"] = bool(review) and review.get("comparisonHash") == result["comparisonHash"]
    return result
