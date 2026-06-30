from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import xlrd


SHARE_PATTERN = re.compile(r"\((\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)\)")
LAND_LEDGER_AGENT_VERSION = "land-ledger-agent-v1.1"
SQM_TO_PY = 0.3025
DEFAULT_PNU_CONFIDENCE_THRESHOLD = 0.85
CURRENT_OFFICIAL_PRICE_BASE_YEAR = 2026
SUPPORTED_WORKFLOW_STATUSES = [
    "uploaded",
    "parsed",
    "parse_failed",
    "parsed_with_warnings",
    "needs_review",
    "needs_enrichment",
    "enrichment_in_progress",
    "enrichment_failed",
    "enriched",
    "needs_legal_evidence",
    "ready_for_legal_review",
    "legal_review_in_progress",
    "legal_review_completed",
    "ready_for_pf_review",
]


class LandLedgerParseError(ValueError):
    """Raised when a land ledger workbook cannot be read (empty/unsupported/corrupt)."""


def _read_workbook_rows(workbook_path: Path) -> tuple[List[List[Any]], str]:
    """Read the first worksheet of a .xls/.xlsx ledger into rows of cell values.

    Supports both legacy .xls (xlrd) and modern .xlsx/.xlsm (openpyxl). Empty
    cells are normalized to "" to match the existing xlrd-tuned row parsing.
    Raises LandLedgerParseError (a ValueError) with a Korean message on any
    read failure so callers can return a graceful 4xx instead of a 500.
    """
    suffix = workbook_path.suffix.lower()
    try:
        if not workbook_path.exists() or workbook_path.stat().st_size == 0:
            raise LandLedgerParseError("빈 파일입니다 (0 bytes). 내용이 있는 토지조서 파일을 올려주세요.")

        if suffix in (".xlsx", ".xlsm"):
            import openpyxl  # lazy: only when an xlsx is uploaded

            workbook = openpyxl.load_workbook(str(workbook_path), read_only=True, data_only=True)
            try:
                worksheet = workbook.worksheets[0]
                rows = [
                    ["" if cell is None else cell for cell in row]
                    for row in worksheet.iter_rows(values_only=True)
                ]
                sheet_name = worksheet.title
            finally:
                workbook.close()
            return rows, sheet_name

        # Legacy .xls (and unknown suffixes attempted as .xls) via xlrd.
        book = xlrd.open_workbook(str(workbook_path))
        sheet = book.sheet_by_index(0)
        rows = [
            [sheet.cell_value(row_index, column_index) for column_index in range(sheet.ncols)]
            for row_index in range(sheet.nrows)
        ]
        return rows, sheet.name
    except LandLedgerParseError:
        raise
    except Exception as exc:  # xlrd.XLRDError, openpyxl/zip errors, etc.
        raise LandLedgerParseError(
            f"토지조서 워크북을 읽지 못했습니다 ({suffix or '확장자 없음'}): {exc}"
        ) from exc


class LandLedgerAgent:
    """Parse Korean land acquisition ledgers into structured parcel data."""

    def enrich_analysis(
        self,
        analysis: Dict[str, Any],
        enrichment_options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Apply batch PNU/official-price/registry enrichment to a parsed ledger result."""
        from agents.land_ledger_enrichment_agent import LandLedgerEnrichmentAgent

        enriched = LandLedgerEnrichmentAgent().enrich(analysis, enrichment_options)
        return self._rebuild_analysis(enriched)

    def analyze_xls(
        self,
        workbook_path: str | Path,
        source_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        workbook_path = Path(workbook_path)
        rows, sheet_name = _read_workbook_rows(workbook_path)
        return self.analyze_rows(
            rows,
            sheet_name=sheet_name,
            source_file=(source_info or {}).get("original_file_name") or workbook_path.name,
            source_info=source_info,
        )

    # Backwards-compatible alias for callers that pass either .xls or .xlsx.
    analyze_workbook = analyze_xls

    def analyze_rows(
        self,
        rows: List[List[Any]],
        *,
        sheet_name: str = "land_ledger",
        source_file: Optional[str] = None,
        source_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        data_rows = rows[4:]
        parcels: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None
        source_totals = _extract_source_totals(rows)

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

        lot_key_counts: Dict[str, int] = {}
        for parcel in parcels:
            lot_key = _parcel_lot_key(parcel)
            if lot_key:
                lot_key_counts[lot_key] = lot_key_counts.get(lot_key, 0) + 1

        for parcel in parcels:
            lot_key = _parcel_lot_key(parcel)
            parcel["duplicateLotImported"] = bool(lot_key and lot_key_counts.get(lot_key, 0) > 1)
            self._finalize_parcel(parcel)
            parcel["qa"] = self._parcel_qa(parcel)
            parcel["issues"] = self._parcel_issues(parcel)
            parcel["workflowStatus"] = self._parcel_workflow_status(parcel)
            parcel["pfReviewMapping"] = self._pf_review_mapping(parcel)

        summary = self._summary(parcels)
        import_consistency = self._validate_import_consistency(parcels, summary, source_totals)
        validation = self._validation(parcels, summary, source_totals)
        import_metadata = self._import_metadata(rows, sheet_name, source_file, source_info)
        import_metadata["validationStatus"] = validation["status"]
        return {
            "analysisType": "land_ledger_import",
            "profile": "legacy-bonri-land-ledger-v1",
            "parserVersion": LAND_LEDGER_AGENT_VERSION,
            "sourceFile": source_file,
            "importMetadata": import_metadata,
            "sheetName": sheet_name,
            "sourceTotals": source_totals,
            "summary": summary,
            "parcels": parcels,
            "validation": validation,
            "importConsistency": import_consistency,
            "landAcquisitionStatus": self._calculate_land_acquisition_status(parcels, summary),
            "pfSummary": self._generate_land_acquisition_pf_summary(parcels, summary),
            "qaProcess": self._qa_process(parcels),
            "enrichmentPlan": self._enrichment_plan(),
            "supportedWorkflowStatuses": SUPPORTED_WORKFLOW_STATUSES,
        }

    def _rebuild_analysis(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
        parcels = analysis.get("parcels", [])
        for parcel in parcels:
            self._finalize_parcel(parcel)
            parcel["qa"] = self._parcel_qa(parcel)
            parcel["issues"] = self._parcel_issues(parcel)
            parcel["workflowStatus"] = self._parcel_workflow_status(parcel)
            parcel["pfReviewMapping"] = self._pf_review_mapping(parcel)

        summary = self._summary(parcels)
        source_totals = analysis.get("sourceTotals", {})
        import_consistency = self._validate_import_consistency(parcels, summary, source_totals)
        validation = self._validation(parcels, summary, source_totals)
        analysis["summary"] = summary
        analysis["importConsistency"] = import_consistency
        analysis["validation"] = validation
        analysis["landAcquisitionStatus"] = self._calculate_land_acquisition_status(parcels, summary)
        analysis["pfSummary"] = self._generate_land_acquisition_pf_summary(parcels, summary)
        analysis["qaProcess"] = self._qa_process(parcels)
        if "importMetadata" in analysis:
            analysis["importMetadata"]["validationStatus"] = validation["status"]
        return analysis

    def _new_parcel(self, row: List[Any], sequence: int, row_index: int) -> Dict[str, Any]:
        dong = _clean_text(_cell(row, 1))
        lot_number = _clean_text(_cell(row, 2))
        lot_parts = _parse_lot_number(lot_number)
        land_category = _clean_text(_cell(row, 3))
        area_sqm = _to_float(_cell(row, 4))
        area_py = _to_float(_cell(row, 5))
        total_contract_amount = _to_float(_cell(row, 8))
        return {
            "sequence": sequence,
            "dong": dong,
            "lotNumber": lot_number,
            "rawLotNumber": lot_number,
            "isMountain": lot_parts["isMountain"],
            "mainLotNo": lot_parts["mainLotNo"],
            "subLotNo": lot_parts["subLotNo"],
            "normalizedLotNumber": lot_parts["normalizedLotNumber"],
            "addressFragment": " ".join(part for part in [dong, lot_number] if part),
            "pnu": None,
            "resolvedPnu": None,
            "resolvedLegalAddress": None,
            "pnuMatchMethod": None,
            "pnuCandidateCount": None,
            "pnuResolverWarning": None,
            "pnuResolveConfidence": None,
            "landCategory": land_category,
            "standardLandCategoryCode": _standard_land_category_code(land_category),
            "areaSqm": area_sqm,
            "areaPy": area_py,
            "officialLandPricePerSqm": None,
            "officialLandPriceBaseYear": None,
            "officialLandPriceBaseDate": None,
            "officialTotalLandValue": None,
            "officialLandPriceSource": None,
            "registryEvidence": None,
            "totalContractAmount": total_contract_amount,
            "contractDate": _clean_text(_cell(row, 16)),
            "depositDueDate": _clean_text(_cell(row, 17)),
            "balanceDueDate": _clean_text(_cell(row, 18)),
            "remarks": _clean_text(_cell(row, 19)),
            "owners": [],
            "evidence": [],
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

    def _import_metadata(
        self,
        rows: List[List[Any]],
        sheet_name: str,
        source_file: Optional[str],
        source_info: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        source_info = source_info or {}
        return {
            "importId": source_info.get("import_id"),
            "projectId": source_info.get("project_id"),
            "originalFileName": source_info.get("original_file_name") or source_file,
            "originalFileHash": source_info.get("original_file_hash"),
            "uploadedBy": source_info.get("uploaded_by"),
            "uploadedAt": source_info.get("uploaded_at"),
            "parserVersion": LAND_LEDGER_AGENT_VERSION,
            "sheetName": sheet_name,
            "rowCount": len(rows),
            "columnCount": max((len(row) for row in rows), default=0),
            "parseStatus": "parsed",
            "validationStatus": "pending",
            "ledgerVersion": source_info.get("ledger_version", 1),
            "previousLedgerVersion": source_info.get("previous_ledger_version"),
            "changeReason": source_info.get("change_reason"),
            "changedBy": source_info.get("changed_by"),
            "changedAt": source_info.get("changed_at"),
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
            "security": {
                "accountNumberStored": False,
                "accountNumberEncrypted": None,
                "accountNumberMasked": _mask_account(_clean_text(_cell(row, 15))),
                "rawAccountRetained": False,
                "auditRequiredForRawAccess": True,
            },
        }

    def _finalize_parcel(self, parcel: Dict[str, Any]) -> None:
        owners = parcel["owners"]
        share_sum = sum(owner["share"] for owner in owners if owner["share"] is not None)
        if len(owners) == 1 and owners[0]["share"] is None:
            owners[0]["share"] = 1.0
            share_sum = 1.0
        for owner in owners:
            owner["paymentStatus"] = _payment_status(owner)
        parcel["ownerCount"] = len(owners)
        parcel["knownShareSum"] = share_sum if share_sum else None
        parcel["paymentSummary"] = _payment_summary(owners)
        parcel["paymentStatus"] = _parcel_payment_status(parcel["paymentSummary"])
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
        lot_number_valid = bool(parcel.get("mainLotNo"))
        duplicate_lot_imported = bool(parcel.get("duplicateLotImported"))
        land_category_mapped = bool(parcel.get("standardLandCategoryCode"))
        parcel_valid = bool(parcel.get("dong") and parcel.get("lotNumber") and parcel.get("landCategory")) and (
            (parcel.get("areaSqm") or 0) > 0
        )
        area_consistent = _area_consistent(parcel.get("areaSqm"), parcel.get("areaPy"))
        ownership_valid = _ownership_valid(parcel)
        payment_schedule_valid = _payment_schedule_valid(parcel)
        pnu_resolved = bool(parcel.get("pnu"))
        pnu_confidence = parcel.get("pnuResolveConfidence")
        pnu_confident = pnu_confidence is None or pnu_confidence >= DEFAULT_PNU_CONFIDENCE_THRESHOLD
        official_price_attached = bool(parcel.get("officialLandPricePerSqm"))
        official_price_current = _official_price_current(parcel)
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
            and pnu_confident
            and official_price_attached
            and official_price_current
            and registry_evidence_attached
            and security_redacted
        )
        return {
            "sourceReconciled": source_reconciled,
            "lotNumberValid": lot_number_valid,
            "duplicateLotImported": duplicate_lot_imported,
            "landCategoryMapped": land_category_mapped,
            "parcelUnitConsistent": parcel_valid and area_consistent and lot_number_valid and land_category_mapped and not duplicate_lot_imported,
            "ownershipShareValid": ownership_valid,
            "paymentScheduleValid": payment_schedule_valid,
            "pnuResolveConfidence": parcel.get("pnuResolveConfidence"),
            "pnuConfidenceAcceptable": bool(pnu_resolved and pnu_confident),
            "officialPriceBaseYear": parcel.get("officialLandPriceBaseYear"),
            "officialPriceCurrent": official_price_current,
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
        if not qa["lotNumberValid"]:
            issues.append(_issue("invalid_lot_number", "blocking", "lotNumber", "Lot number could not be normalized into main/sub lot numbers."))
        if qa["duplicateLotImported"]:
            issues.append(_issue("duplicate_lot_number", "blocking", "lotNumber", "The same dong/lot number was imported more than once."))
        if not qa["landCategoryMapped"]:
            issues.append(_issue("unmapped_land_category", "caution", "landCategory", "Land category could not be mapped to a standard Korean land category code."))
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
        elif not qa["officialPriceCurrent"]:
            issues.append(_issue("outdated_official_land_price", "caution", "official_price", "Official land price exists but base year is outdated or missing."))
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
            return "uploaded"
        if not qa["parcelUnitConsistent"]:
            return "parsed_with_warnings"
        if not qa["ownershipShareValid"]:
            return "needs_review"
        if not qa["paymentScheduleValid"]:
            return "needs_review"
        if parcel.get("pnu") and not qa["pnuConfidenceAcceptable"]:
            return "needs_review"
        if not parcel.get("pnu"):
            return "needs_enrichment"
        if not parcel.get("officialLandPricePerSqm"):
            return "needs_enrichment"
        if not qa["officialPriceCurrent"]:
            return "needs_enrichment"
        if not parcel.get("registryEvidence"):
            return "needs_legal_evidence"
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
                "officialLandPricePerSqm": parcel.get("officialLandPricePerSqm"),
                "officialLandPriceBaseYear": parcel.get("officialLandPriceBaseYear"),
                "paymentStatus": parcel.get("paymentStatus"),
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
        owner_records = [owner for parcel in parcels for owner in parcel.get("owners", [])]
        payment_totals = {
            "individualContractAmountTotal": sum(owner.get("individualContractAmount") or 0 for owner in owner_records),
            "depositPaidTotal": sum(owner.get("depositPaid") or 0 for owner in owner_records),
            "depositUnpaidTotal": sum(owner.get("depositUnpaid") or 0 for owner in owner_records),
            "interimPaymentTotal": sum(owner.get("interimPayment") or 0 for owner in owner_records),
            "balancePaymentTotal": sum(owner.get("balancePayment") or 0 for owner in owner_records),
        }
        return {
            "parcelCount": len(parcels),
            "ownerRecordCount": sum(len(parcel["owners"]) for parcel in parcels),
            "totalAreaSqm": sum(parcel.get("areaSqm") or 0 for parcel in parcels),
            "totalAreaPy": sum(parcel.get("areaPy") or 0 for parcel in parcels),
            "totalContractAmount": sum(parcel.get("totalContractAmount") or 0 for parcel in parcels),
            "paymentTotals": payment_totals,
            "missingPnuCount": sum(1 for parcel in parcels if not parcel.get("pnu")),
            "missingOfficialLandPriceCount": sum(1 for parcel in parcels if not parcel.get("officialLandPricePerSqm")),
            "registryEvidenceMissingCount": sum(1 for parcel in parcels if not parcel.get("registryEvidence")),
            "pfOutputReadyCount": sum(1 for parcel in parcels if parcel.get("qa", {}).get("pfOutputReady")),
            "workflowStatusCounts": status_counts,
        }

    def _validate_import_consistency(
        self,
        parcels: List[Dict[str, Any]],
        summary: Dict[str, Any],
        source_totals: Dict[str, Any],
    ) -> Dict[str, Any]:
        checks = []
        if source_totals.get("totalAreaSqm") is not None:
            checks.append(_numeric_check("total_area_sqm", summary["totalAreaSqm"], source_totals["totalAreaSqm"], 0.1))
        if source_totals.get("totalAreaPy") is not None:
            checks.append(_numeric_check("total_area_py", summary["totalAreaPy"], source_totals["totalAreaPy"], 0.1))
        if source_totals.get("totalContractAmount") is not None:
            checks.append(_numeric_check("total_contract_amount", summary["totalContractAmount"], source_totals["totalContractAmount"], 1.0))
        payment_totals = summary["paymentTotals"]
        for key in ["depositPaidTotal", "depositUnpaidTotal", "interimPaymentTotal", "balancePaymentTotal"]:
            if source_totals.get(key) is not None:
                checks.append(_numeric_check(key, payment_totals[key], source_totals[key], 1.0))
        parcel_amount_sum = sum(parcel.get("totalContractAmount") or 0 for parcel in parcels)
        individual_amount_sum = payment_totals["individualContractAmountTotal"]
        if individual_amount_sum:
            checks.append(_numeric_check("parcel_amount_sum_vs_owner_amount_sum", parcel_amount_sum, individual_amount_sum, 1.0))

        failed = [check for check in checks if not check["passed"]]
        return {
            "status": "needs_review" if failed else "passed",
            "checks": checks,
            "failedChecks": failed,
        }

    def _validation(
        self,
        parcels: List[Dict[str, Any]],
        summary: Dict[str, Any],
        source_totals: Dict[str, Any],
    ) -> Dict[str, Any]:
        issues = []
        import_consistency = self._validate_import_consistency(parcels, summary, source_totals)
        for check in import_consistency["failedChecks"]:
            issues.append(_issue(
                "import_consistency_failed",
                "blocking",
                check["name"],
                f"Parsed value {check['parsedValue']} does not match source total {check['sourceValue']}.",
            ))
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
        lifecycle_status = "ready_for_pf_review"
        if import_consistency["status"] == "needs_review" or any(issue["severity"] == "blocking" for issue in issues):
            lifecycle_status = "needs_review"
        elif summary["missingPnuCount"] or summary["missingOfficialLandPriceCount"]:
            lifecycle_status = "needs_enrichment"
        elif summary["registryEvidenceMissingCount"]:
            lifecycle_status = "needs_legal_evidence"
        return {
            "status": lifecycle_status,
            "highestSeverity": highest,
            "issues": issues,
        }

    def _calculate_land_acquisition_status(
        self,
        parcels: List[Dict[str, Any]],
        summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        contracted = [parcel for parcel in parcels if parcel.get("totalContractAmount") or parcel.get("owners")]
        deposit_paid = [
            parcel for parcel in parcels
            if (parcel.get("paymentSummary", {}).get("depositPaidTotal") or 0) > 0
            or (parcel.get("paymentSummary", {}).get("depositUnpaidTotal") or 0) > 0
        ]
        balance_paid = [
            parcel for parcel in parcels
            if parcel.get("paymentStatus") == "fully_paid"
        ]
        public_land = [
            parcel for parcel in parcels
            if parcel.get("qa", {}).get("specialParcelException") == "public_land"
        ]
        return {
            "totalRequiredParcels": len(parcels),
            "contractedParcels": len(contracted),
            "paidDepositParcels": len(deposit_paid),
            "balancePaidParcels": len(balance_paid),
            "ownershipTransferredParcels": sum(1 for parcel in parcels if (parcel.get("registryEvidence") or {}).get("ownerMatchStatus") == "matched"),
            "trustedParcels": sum(1 for parcel in parcels if (parcel.get("registryEvidence") or {}).get("trustRegistrationExists")),
            "missingParcels": max(0, len(parcels) - len(contracted)),
            "privateLandArea": summary["totalAreaSqm"] - sum(parcel.get("areaSqm") or 0 for parcel in public_land),
            "publicLandArea": sum(parcel.get("areaSqm") or 0 for parcel in public_land),
            "acquisitionAreaRatio": len(contracted) / len(parcels) if parcels else 0,
            "acquisitionAmountRatio": (
                sum(parcel.get("totalContractAmount") or 0 for parcel in contracted) / summary["totalContractAmount"]
                if summary["totalContractAmount"] else 0
            ),
            "publicLandDetected": bool(public_land),
            "publicLandPurchaseStatus": "public_land_purchase_required" if public_land else "not_applicable",
        }

    def _generate_land_acquisition_pf_summary(
        self,
        parcels: List[Dict[str, Any]],
        summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        pnu_ratio = 1 - (summary["missingPnuCount"] / summary["parcelCount"]) if summary["parcelCount"] else 0
        official_price_ratio = 1 - (summary["missingOfficialLandPriceCount"] / summary["parcelCount"]) if summary["parcelCount"] else 0
        registry_ratio = 1 - (summary["registryEvidenceMissingCount"] / summary["parcelCount"]) if summary["parcelCount"] else 0
        balance_due_amount = sum(parcel.get("paymentSummary", {}).get("balancePaymentTotal") or 0 for parcel in parcels)
        deposit_paid_parcels = sum(1 for parcel in parcels if (parcel.get("paymentSummary", {}).get("depositPaidTotal") or 0) > 0)
        return {
            "totalParcels": summary["parcelCount"],
            "totalAreaSqm": summary["totalAreaSqm"],
            "totalPurchaseAmount": summary["totalContractAmount"],
            "pnuCoverageRatio": pnu_ratio,
            "officialLandPriceCoverageRatio": official_price_ratio,
            "registryEvidenceCoverageRatio": registry_ratio,
            "depositPaidParcelRatio": deposit_paid_parcels / summary["parcelCount"] if summary["parcelCount"] else 0,
            "balanceDueAmount": balance_due_amount,
            "ownershipTransferredParcels": sum(1 for parcel in parcels if (parcel.get("registryEvidence") or {}).get("ownerMatchStatus") == "matched"),
            "restrictionReviewRequiredParcels": summary["registryEvidenceMissingCount"],
            "status": "needs_enrichment" if pnu_ratio < 1 or official_price_ratio < 1 else "needs_legal_evidence" if registry_ratio < 1 else "ready_for_pf_review",
            "reviewMemo": (
                "This land ledger can support preliminary parcel, owner, area, purchase amount, and payment schedule review, "
                "but it cannot be used as final legal-review or PF-approval evidence until PNU, official land price, "
                "cadastral characteristics, and registry/encumbrance evidence are attached."
            ),
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


def _extract_source_totals(rows: List[List[Any]]) -> Dict[str, Any]:
    total_row = next((row for row in rows if _is_total_row(row)), None)
    if not total_row:
        return {
            "present": False,
            "totalAreaSqm": None,
            "totalAreaPy": None,
            "totalContractAmount": None,
            "depositPaidTotal": None,
            "depositUnpaidTotal": None,
            "interimPaymentTotal": None,
            "balancePaymentTotal": None,
        }
    return {
        "present": True,
        "rowLabel": _cell(total_row, 0),
        "totalAreaSqm": _to_float(_cell(total_row, 4)),
        "totalAreaPy": _to_float(_cell(total_row, 5)),
        "totalContractAmount": _to_float(_cell(total_row, 8)),
        "depositPaidTotal": _to_float(_cell(total_row, 9)),
        "depositUnpaidTotal": _to_float(_cell(total_row, 10)),
        "interimPaymentTotal": _to_float(_cell(total_row, 11)),
        "balancePaymentTotal": _to_float(_cell(total_row, 12)),
    }


def _numeric_check(name: str, parsed_value: float, source_value: float, tolerance: float) -> Dict[str, Any]:
    diff = parsed_value - source_value
    return {
        "name": name,
        "parsedValue": parsed_value,
        "sourceValue": source_value,
        "diff": diff,
        "tolerance": tolerance,
        "passed": abs(diff) <= tolerance,
    }


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


def _parse_lot_number(lot_number: Optional[str]) -> Dict[str, Any]:
    text = str(lot_number or "").strip()
    text = text.replace("\ubc88\uc9c0", "").strip()
    match = re.search(r"(\uc0b0)?\s*(\d+)(?:-(\d+))?", text)
    if not match:
        return {
            "isMountain": False,
            "mainLotNo": None,
            "subLotNo": None,
            "normalizedLotNumber": None,
        }
    is_mountain = bool(match.group(1))
    main = int(match.group(2))
    sub = int(match.group(3)) if match.group(3) else 0
    prefix = "\uc0b0" if is_mountain else ""
    normalized = f"{prefix}{main}" + (f"-{sub}" if sub else "")
    return {
        "isMountain": is_mountain,
        "mainLotNo": main,
        "subLotNo": sub,
        "normalizedLotNumber": normalized,
    }


def _parcel_lot_key(parcel: Dict[str, Any]) -> Optional[str]:
    normalized = parcel.get("normalizedLotNumber")
    if not normalized:
        return None
    dong = parcel.get("dong") or ""
    return f"{dong}|{normalized}"


def _standard_land_category_code(land_category: Optional[str]) -> Optional[str]:
    if not land_category:
        return None
    mapping = {
        "\uc804": "01",
        "\ub2f5": "02",
        "\uacfc\uc218\uc6d0": "03",
        "\ubaa9\uc7a5\uc6a9\uc9c0": "04",
        "\uc784\uc57c": "05",
        "\uad11\ucc9c\uc9c0": "06",
        "\uc5fc\uc804": "07",
        "\ub300": "08",
        "\uacf5\uc7a5\uc6a9\uc9c0": "09",
        "\ud559\uad50\uc6a9\uc9c0": "10",
        "\uc8fc\ucc28\uc7a5": "11",
        "\uc8fc\uc720\uc18c\uc6a9\uc9c0": "12",
        "\ucc3d\uace0\uc6a9\uc9c0": "13",
        "\ub3c4\ub85c": "14",
        "\ucca0\ub3c4\uc6a9\uc9c0": "15",
        "\uc81c\ubc29": "16",
        "\ud558\ucc9c": "17",
        "\uad6c\uac70": "18",
        "\uc720\uc9c0": "19",
        "\uc591\uc5b4\uc7a5": "20",
        "\uc218\ub3c4\uc6a9\uc9c0": "21",
        "\uacf5\uc6d0": "22",
        "\uccb4\uc721\uc6a9\uc9c0": "23",
        "\uc720\uc6d0\uc9c0": "24",
        "\uc885\uad50\uc6a9\uc9c0": "25",
        "\uc0ac\uc801\uc9c0": "26",
        "\ubb18\uc9c0": "27",
        "\uc7a1\uc885\uc9c0": "28",
    }
    return mapping.get(str(land_category).strip())


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
    return abs(area_py - area_sqm * SQM_TO_PY) <= 0.5


def _official_price_current(parcel: Dict[str, Any]) -> bool:
    if not parcel.get("officialLandPricePerSqm"):
        return False
    base_year = parcel.get("officialLandPriceBaseYear")
    if not base_year:
        return False
    return int(base_year) >= CURRENT_OFFICIAL_PRICE_BASE_YEAR - 1


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


def _payment_summary(owners: List[Dict[str, Any]]) -> Dict[str, float]:
    return {
        "individualContractAmountTotal": sum(owner.get("individualContractAmount") or 0 for owner in owners),
        "depositPaidTotal": sum(owner.get("depositPaid") or 0 for owner in owners),
        "depositUnpaidTotal": sum(owner.get("depositUnpaid") or 0 for owner in owners),
        "interimPaymentTotal": sum(owner.get("interimPayment") or 0 for owner in owners),
        "balancePaymentTotal": sum(owner.get("balancePayment") or 0 for owner in owners),
    }


def _payment_status(owner: Dict[str, Any]) -> str:
    amount = owner.get("individualContractAmount") or 0
    paid = sum(owner.get(key) or 0 for key in ["depositPaid", "depositUnpaid", "interimPayment", "balancePayment"])
    if not amount:
        return "payment_not_started"
    if paid >= amount - 1:
        return "fully_paid"
    if (owner.get("balancePayment") or 0) > 0:
        return "balance_due"
    if (owner.get("interimPayment") or 0) > 0:
        return "interim_paid"
    if (owner.get("depositPaid") or 0) > 0 or (owner.get("depositUnpaid") or 0) > 0:
        return "deposit_paid"
    return "payment_not_started"


def _parcel_payment_status(payment_summary: Dict[str, float]) -> str:
    amount = payment_summary.get("individualContractAmountTotal") or 0
    paid = sum(
        payment_summary.get(key) or 0
        for key in ["depositPaidTotal", "depositUnpaidTotal", "interimPaymentTotal", "balancePaymentTotal"]
    )
    if not amount:
        return "payment_not_started"
    if paid >= amount - 1:
        return "fully_paid"
    if (payment_summary.get("balancePaymentTotal") or 0) > 0:
        return "balance_due"
    if (payment_summary.get("interimPaymentTotal") or 0) > 0:
        return "interim_paid"
    if (payment_summary.get("depositPaidTotal") or 0) > 0 or (payment_summary.get("depositUnpaidTotal") or 0) > 0:
        return "deposit_paid"
    return "payment_not_started"


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
