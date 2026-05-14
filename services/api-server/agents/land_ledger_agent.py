from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import xlrd


SHARE_PATTERN = re.compile(r"\((\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)\)")


class LandLedgerAgent:
    """Parse Korean land acquisition ledgers into structured parcel data."""

    def analyze_xls(self, workbook_path: str | Path) -> Dict[str, Any]:
        workbook_path = Path(workbook_path)
        book = xlrd.open_workbook(str(workbook_path))
        sheet = book.sheet_by_index(0)
        rows = [
            [sheet.cell_value(row_index, column_index) for column_index in range(sheet.ncols)]
            for row_index in range(sheet.nrows)
        ]
        return self.analyze_rows(rows, sheet_name=sheet.name, source_file=workbook_path.name)

    def analyze_rows(
        self,
        rows: List[List[Any]],
        *,
        sheet_name: str = "land_ledger",
        source_file: Optional[str] = None,
    ) -> Dict[str, Any]:
        data_rows = rows[4:]
        parcels: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None

        for row_index, row in enumerate(data_rows, start=5):
            if _is_total_row(row):
                continue

            sequence = _to_int(_cell(row, 0))
            if sequence is not None:
                current = self._new_parcel(row, sequence, row_index)
                parcels.append(current)

            if current is None:
                continue

            owner = str(_cell(row, 6) or "").strip()
            if owner:
                current["owners"].append(self._owner_record(row, row_index))

        for parcel in parcels:
            self._finalize_parcel(parcel)
            parcel["qa"] = self._parcel_qa(parcel)
            parcel["issues"] = self._parcel_issues(parcel)
            parcel["workflowStatus"] = self._parcel_workflow_status(parcel)
            parcel["pfReviewMapping"] = self._pf_review_mapping(parcel)

        summary = self._summary(parcels)
        return {
            "analysisType": "land_ledger_import",
            "profile": "legacy-bonri-land-ledger-v1",
            "sourceFile": source_file,
            "sheetName": sheet_name,
            "summary": summary,
            "parcels": parcels,
            "validation": self._validation(parcels, summary),
            "qaProcess": self._qa_process(parcels),
            "enrichmentPlan": self._enrichment_plan(),
        }

    def _new_parcel(self, row: List[Any], sequence: int, row_index: int) -> Dict[str, Any]:
        dong = _clean_text(_cell(row, 1))
        lot_number = _clean_text(_cell(row, 2))
        land_category = _clean_text(_cell(row, 3))
        area_sqm = _to_float(_cell(row, 4))
        area_py = _to_float(_cell(row, 5))
        total_contract_amount = _to_float(_cell(row, 8))
        return {
            "sequence": sequence,
            "dong": dong,
            "lotNumber": lot_number,
            "addressFragment": " ".join(part for part in [dong, lot_number] if part),
            "pnu": None,
            "pnuResolveConfidence": None,
            "landCategory": land_category,
            "areaSqm": area_sqm,
            "areaPy": area_py,
            "officialLandPricePerSqm": None,
            "officialLandPriceBaseYear": None,
            "officialLandPriceBaseDate": None,
            "registryEvidence": None,
            "totalContractAmount": total_contract_amount,
            "contractDate": _clean_text(_cell(row, 16)),
            "depositDueDate": _clean_text(_cell(row, 17)),
            "balanceDueDate": _clean_text(_cell(row, 18)),
            "remarks": _clean_text(_cell(row, 19)),
            "owners": [],
            "source": {
                "row": row_index,
                "sheet": "legacy-bonri-land-ledger-v1",
                "rawCells": {
                    "sequence": _cell(row, 0),
                    "dong": _cell(row, 1),
                    "lotNumber": _cell(row, 2),
                    "landCategory": _cell(row, 3),
                    "areaSqm": _cell(row, 4),
                    "areaPy": _cell(row, 5),
                    "totalContractAmount": _cell(row, 8),
                },
            },
        }

    def _owner_record(self, row: List[Any], row_index: int) -> Dict[str, Any]:
        owner_text = _clean_text(_cell(row, 6))
        share = _parse_share(owner_text)
        return {
            "ownerName": _strip_share(owner_text),
            "rawOwnerText": owner_text,
            "share": share,
            "assetHint": _asset_hint(owner_text),
            "individualContractAmount": _to_float(_cell(row, 7)),
            "depositPaid": _to_float(_cell(row, 9)),
            "depositUnpaid": _to_float(_cell(row, 10)),
            "interimPayment": _to_float(_cell(row, 11)),
            "balancePayment": _to_float(_cell(row, 12)),
            "accountHolder": _clean_text(_cell(row, 13)),
            "bankName": _clean_text(_cell(row, 14)),
            "accountNumberMasked": _mask_account(_clean_text(_cell(row, 15))),
            "contractDate": _clean_text(_cell(row, 16)),
            "depositDueDate": _clean_text(_cell(row, 17)),
            "balanceDueDate": _clean_text(_cell(row, 18)),
            "remarks": _clean_text(_cell(row, 19)),
            "source": {
                "row": row_index,
                "rawCells": {
                    "owner": _cell(row, 6),
                    "individualContractAmount": _cell(row, 7),
                    "depositPaid": _cell(row, 9),
                    "depositUnpaid": _cell(row, 10),
                    "interimPayment": _cell(row, 11),
                    "balancePayment": _cell(row, 12),
                },
            },
        }

    def _finalize_parcel(self, parcel: Dict[str, Any]) -> None:
        owners = parcel["owners"]
        share_sum = sum(owner["share"] for owner in owners if owner["share"] is not None)
        if len(owners) == 1 and owners[0]["share"] is None:
            owners[0]["share"] = 1.0
            share_sum = 1.0
        parcel["ownerCount"] = len(owners)
        parcel["knownShareSum"] = share_sum if share_sum else None
        parcel["missingFields"] = [
            field
            for field, value in [
                ("pnu", parcel.get("pnu")),
                ("officialLandPricePerSqm", parcel.get("officialLandPricePerSqm")),
                ("officialLandPriceBaseYear", parcel.get("officialLandPriceBaseYear")),
                ("officialLandPriceBaseDate", parcel.get("officialLandPriceBaseDate")),
                ("registryEvidence", parcel.get("registryEvidence")),
            ]
            if value in (None, "")
        ]
        if owners and any(owner["share"] is None for owner in owners):
            parcel["missingFields"].append("owner.share")

    def _parcel_qa(self, parcel: Dict[str, Any]) -> Dict[str, Any]:
        source_reconciled = bool(parcel.get("source", {}).get("row")) and bool(parcel.get("addressFragment"))
        parcel_valid = bool(parcel.get("dong") and parcel.get("lotNumber") and parcel.get("landCategory")) and (
            (parcel.get("areaSqm") or 0) > 0
        )
        area_consistent = _area_consistent(parcel.get("areaSqm"), parcel.get("areaPy"))
        ownership_valid = _ownership_valid(parcel)
        payment_schedule_valid = _payment_schedule_valid(parcel)
        pnu_resolved = bool(parcel.get("pnu"))
        official_price_attached = bool(parcel.get("officialLandPricePerSqm"))
        registry_evidence_attached = bool(parcel.get("registryEvidence"))
        special_exception = _special_parcel_exception(parcel)
        security_redacted = all(
            not owner.get("accountNumberMasked") or owner["accountNumberMasked"].startswith("****")
            for owner in parcel.get("owners", [])
        )
        pf_output_ready = (
            source_reconciled
            and parcel_valid
            and area_consistent
            and ownership_valid
            and payment_schedule_valid
            and pnu_resolved
            and official_price_attached
            and registry_evidence_attached
            and security_redacted
        )
        return {
            "sourceReconciled": source_reconciled,
            "parcelUnitConsistent": parcel_valid and area_consistent,
            "ownershipShareValid": ownership_valid,
            "paymentScheduleValid": payment_schedule_valid,
            "pnuResolveConfidence": parcel.get("pnuResolveConfidence"),
            "officialPriceBaseYear": parcel.get("officialLandPriceBaseYear"),
            "registryEvidenceStatus": "attached" if registry_evidence_attached else "missing",
            "specialParcelException": special_exception,
            "securityRedactionStatus": "redacted" if security_redacted else "raw_sensitive_data_present",
            "pfOutputReady": pf_output_ready,
        }

    def _parcel_issues(self, parcel: Dict[str, Any]) -> List[Dict[str, Any]]:
        issues = []
        qa = parcel["qa"]
        if not qa["sourceReconciled"]:
            issues.append(_issue("source_not_reconciled", "blocking", "source", "Original row and parsed parcel values could not be reconciled."))
        if not qa["parcelUnitConsistent"]:
            issues.append(_issue("parcel_unit_inconsistent", "blocking", "parcel", "Parcel dong, lot number, land category, area, or sqm/py conversion is inconsistent."))
        if not qa["ownershipShareValid"]:
            issues.append(_issue("ownership_share_incomplete", "caution", "ownership", "Owner share data is incomplete or does not sum to 100%."))
        if not qa["paymentScheduleValid"]:
            issues.append(_issue("payment_schedule_inconsistent", "blocking", "payment", "Owner-level payments do not reconcile to purchase amounts or parcel total amount."))
        if not parcel.get("pnu"):
            issues.append(_issue("missing_pnu", "blocking_for_enrichment", "pnu", "PNU is missing, so cadastral and land-use enrichment cannot run."))
        if parcel.get("pnu") and (parcel.get("pnuResolveConfidence") or 0) < 0.85:
            issues.append(_issue("low_pnu_confidence", "caution", "pnu", "PNU resolution confidence is below the review threshold."))
        if not parcel.get("officialLandPricePerSqm"):
            issues.append(_issue("missing_official_land_price", "caution", "official_price", "Official land price is missing and must be fetched by base year."))
        if not parcel.get("registryEvidence"):
            issues.append(_issue("missing_registry_evidence", "blocking_for_pf_review", "registry", "Registry and encumbrance evidence is missing."))
        if qa["specialParcelException"]:
            issues.append(_issue("special_parcel_exception", "caution", "exception", f"Special parcel handling required: {qa['specialParcelException']}"))
        if qa["securityRedactionStatus"] != "redacted":
            issues.append(_issue("sensitive_data_unredacted", "blocking", "security", "Sensitive account or personal data is not redacted."))
        return issues

    def _parcel_workflow_status(self, parcel: Dict[str, Any]) -> str:
        qa = parcel["qa"]
        if not qa["sourceReconciled"]:
            return "imported"
        if not qa["parcelUnitConsistent"]:
            return "parsed"
        if not qa["ownershipShareValid"]:
            return "parcel_validated"
        if not qa["paymentScheduleValid"]:
            return "ownership_validated"
        if not parcel.get("pnu"):
            return "needs_pnu_resolution"
        if not parcel.get("officialLandPricePerSqm"):
            return "needs_official_price"
        if not parcel.get("registryEvidence"):
            return "needs_registry_evidence"
        if qa["pfOutputReady"]:
            return "ready_for_pf_review"
        return "enriched"

    def _pf_review_mapping(self, parcel: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "landSecuring": {
                "parcel": parcel.get("addressFragment"),
                "pnu": parcel.get("pnu"),
                "landCategory": parcel.get("landCategory"),
                "areaSqm": parcel.get("areaSqm"),
                "owners": [
                    {
                        "ownerName": owner.get("ownerName"),
                        "share": owner.get("share"),
                        "assetHint": owner.get("assetHint"),
                    }
                    for owner in parcel.get("owners", [])
                ],
                "mortgage": parcel.get("registryEvidence", {}).get("mortgage") if parcel.get("registryEvidence") else None,
                "status": parcel.get("workflowStatus"),
            },
            "legalPermitReview": {
                "requiresRegistryEvidence": not bool(parcel.get("registryEvidence")),
                "requiresPnuResolution": not bool(parcel.get("pnu")),
                "requiresOfficialPrice": not bool(parcel.get("officialLandPricePerSqm")),
            },
            "riskMatrix": {
                "landRisk": "high" if parcel.get("issues") else "low",
                "blockingIssueCodes": [
                    issue["code"]
                    for issue in parcel.get("issues", [])
                    if issue["severity"] in {"blocking", "blocking_for_enrichment", "blocking_for_pf_review"}
                ],
            },
        }

    def _summary(self, parcels: List[Dict[str, Any]]) -> Dict[str, Any]:
        status_counts: Dict[str, int] = {}
        for parcel in parcels:
            status = parcel.get("workflowStatus", "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
        return {
            "parcelCount": len(parcels),
            "ownerRecordCount": sum(len(parcel["owners"]) for parcel in parcels),
            "totalAreaSqm": sum(parcel.get("areaSqm") or 0 for parcel in parcels),
            "totalAreaPy": sum(parcel.get("areaPy") or 0 for parcel in parcels),
            "totalContractAmount": sum(parcel.get("totalContractAmount") or 0 for parcel in parcels),
            "missingPnuCount": sum(1 for parcel in parcels if not parcel.get("pnu")),
            "missingOfficialLandPriceCount": sum(1 for parcel in parcels if not parcel.get("officialLandPricePerSqm")),
            "registryEvidenceMissingCount": sum(1 for parcel in parcels if not parcel.get("registryEvidence")),
            "pfOutputReadyCount": sum(1 for parcel in parcels if parcel.get("qa", {}).get("pfOutputReady")),
            "workflowStatusCounts": status_counts,
        }

    def _validation(self, parcels: List[Dict[str, Any]], summary: Dict[str, Any]) -> Dict[str, Any]:
        issues = []
        if summary["missingPnuCount"]:
            issues.append(_issue(
                "missing_pnu",
                "blocking_for_enrichment",
                "pnu",
                "PNU is missing for imported parcels and must be resolved from dong/lot number before public API enrichment.",
            ))
        if summary["missingOfficialLandPriceCount"]:
            issues.append(_issue(
                "missing_official_land_price",
                "caution",
                "officialLandPricePerSqm",
                "Official land price is not present in the source ledger and requires an external connector.",
            ))
        for parcel in parcels:
            issues.extend(parcel.get("issues", []))
        severity_rank = {"blocking": 3, "blocking_for_enrichment": 3, "blocking_for_pf_review": 3, "caution": 2, "info": 1}
        highest = max((severity_rank.get(issue["severity"], 0) for issue in issues), default=0)
        return {
            "status": "needs_enrichment" if issues else "ready_for_pf_review",
            "highestSeverity": highest,
            "issues": issues,
        }

    def _qa_process(self, parcels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        definitions = [
            ("source_reconciliation", "Original vs parsed value reconciliation", "sourceReconciled"),
            ("parcel_consistency", "Parcel-level dong/lot/category/area consistency", "parcelUnitConsistent"),
            ("ownership_share", "Owner and ownership share validation", "ownershipShareValid"),
            ("payment_schedule", "Purchase amount and payment schedule validation", "paymentScheduleValid"),
            ("pnu_confidence", "PNU resolve confidence score", "pnuResolveConfidence"),
            ("official_price_base_year", "Official land price base-year management", "officialPriceBaseYear"),
            ("registry_evidence", "Registry and encumbrance evidence management", "registryEvidenceStatus"),
            ("special_parcel_exceptions", "Public/shared/special parcel exception handling", "specialParcelException"),
            ("security_redaction", "Personal data and account security policy", "securityRedactionStatus"),
            ("pf_output_mapping", "PF review output auto-linking", "pfOutputReady"),
        ]
        process = []
        for key, title, qa_field in definitions:
            passed = 0
            pending = 0
            for parcel in parcels:
                value = parcel.get("qa", {}).get(qa_field)
                if value in (True, "redacted", "attached") or (qa_field == "specialParcelException" and value is None):
                    passed += 1
                else:
                    pending += 1
            process.append({
                "key": key,
                "title": title,
                "passedCount": passed,
                "pendingCount": pending,
                "status": "passed" if pending == 0 else "pending",
            })
        return process

    def _enrichment_plan(self) -> List[Dict[str, Any]]:
        return [
            {
                "step": "resolve_pnu",
                "required": True,
                "inputFields": ["dong", "lotNumber", "addressFragment"],
                "outputFields": ["pnu", "legalAddress", "pnuResolveConfidence"],
                "note": "Use cadastral/address matching. The sample workbook does not contain 19-digit PNU values.",
            },
            {
                "step": "fetch_land_characteristics",
                "required": True,
                "inputFields": ["pnu"],
                "outputFields": ["landCategory", "areaSqm", "zoning", "landUseZones"],
                "note": "Current frontend PNU lookup covers parts of this for one PNU, but backend batch enrichment is not implemented yet.",
            },
            {
                "step": "fetch_official_land_price",
                "required": True,
                "inputFields": ["pnu", "baseYear"],
                "outputFields": ["officialLandPricePerSqm", "officialLandPriceBaseYear", "officialLandPriceBaseDate"],
                "note": "Requires a separate official land price connector; it is not present in the current codebase.",
            },
            {
                "step": "verify_registry_and_encumbrances",
                "required": True,
                "inputFields": ["pnu", "ownerName"],
                "outputFields": ["registeredOwner", "share", "mortgage", "seizure", "easement", "trustTransferStatus"],
                "note": "Registry/encumbrance evidence is not available from the sample ledger and usually requires registry documents or a separate licensed source.",
            },
        ]


def _cell(row: List[Any], index: int) -> Any:
    return row[index] if index < len(row) else None


def _is_total_row(row: List[Any]) -> bool:
    first = str(_cell(row, 0) or "").strip()
    return first.startswith("\ud569")


def _to_float(value: Any) -> Optional[float]:
    if value in ("", None):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(value: Any) -> Optional[int]:
    numeric = _to_float(value)
    if numeric is None:
        return None
    return int(numeric)


def _clean_text(value: Any) -> Optional[str]:
    if value in ("", None):
        return None
    return str(value).strip()


def _parse_share(owner_text: Optional[str]) -> Optional[float]:
    if not owner_text:
        return None
    match = SHARE_PATTERN.search(owner_text)
    if not match:
        return None
    numerator = float(match.group(1))
    denominator = float(match.group(2))
    return numerator / denominator if denominator else None


def _strip_share(owner_text: Optional[str]) -> Optional[str]:
    if not owner_text:
        return None
    return SHARE_PATTERN.sub("", owner_text).strip()


def _asset_hint(owner_text: Optional[str]) -> Optional[str]:
    if not owner_text:
        return None
    if "\uac74\ubb3c" in owner_text:
        return "building"
    if "\ud1a0\uc9c0" in owner_text:
        return "land"
    return None


def _mask_account(account_number: Optional[str]) -> Optional[str]:
    if not account_number:
        return None
    digits = [char for char in account_number if char.isdigit()]
    if len(digits) <= 4:
        return "****"
    visible = "".join(digits[-4:])
    return f"****{visible}"


def _area_consistent(area_sqm: Optional[float], area_py: Optional[float]) -> bool:
    if area_sqm is None or area_py is None:
        return False
    return abs(area_py - area_sqm * 0.3025) <= 0.5


def _ownership_valid(parcel: Dict[str, Any]) -> bool:
    owners = parcel.get("owners", [])
    if not owners:
        return False
    shares = [owner.get("share") for owner in owners]
    if len(owners) == 1 and shares[0] == 1.0:
        return True
    if any(share is None for share in shares):
        return False
    return abs(sum(shares) - 1.0) <= 0.01


def _payment_schedule_valid(parcel: Dict[str, Any]) -> bool:
    owners = parcel.get("owners", [])
    if not owners:
        return False
    total_contract_amount = parcel.get("totalContractAmount")
    individual_sum = sum(owner.get("individualContractAmount") or 0 for owner in owners)
    if total_contract_amount and individual_sum and abs(individual_sum - total_contract_amount) > 1:
        return False
    for owner in owners:
        amount = owner.get("individualContractAmount")
        if not amount:
            continue
        payments = sum(
            owner.get(key) or 0
            for key in ["depositPaid", "depositUnpaid", "interimPayment", "balancePayment"]
        )
        if payments and abs(payments - amount) > 1:
            return False
    return True


def _special_parcel_exception(parcel: Dict[str, Any]) -> Optional[str]:
    remarks = " ".join(
        str(value or "")
        for value in [
            parcel.get("remarks"),
            *[owner.get("remarks") for owner in parcel.get("owners", [])],
        ]
    )
    if "\uad6d\uacf5\uc720" in remarks:
        return "public_land"
    if "\uc81c\ucc99" in remarks:
        return "excluded_area"
    if "\uac74\ubb3c" in remarks or any(owner.get("assetHint") == "building" for owner in parcel.get("owners", [])):
        return "separate_building_owner"
    if len(parcel.get("owners", [])) > 1:
        return "co_owned_share"
    return None


def _issue(code: str, severity: str, field: str, message: str) -> Dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "field": field,
        "message": message,
    }
