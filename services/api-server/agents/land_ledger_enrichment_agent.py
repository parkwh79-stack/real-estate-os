from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agents.land_ledger_agent import CURRENT_OFFICIAL_PRICE_BASE_YEAR, DEFAULT_PNU_CONFIDENCE_THRESHOLD


LAND_LEDGER_ENRICHMENT_AGENT_VERSION = "land-ledger-enrichment-agent-v1.0"


class LandLedgerEnrichmentAgent:
    """Enrich parsed land-ledger parcels with PNU, cadastral, price, and registry evidence."""

    def enrich(
        self,
        analysis: Dict[str, Any],
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        options = options or {}
        started_at = _now()
        legal_dong_code = options.get("legalDongCode") or options.get("legal_dong_code")
        pnu_records = _index_records(options.get("pnuResolutions") or options.get("pnu_resolutions") or [])
        cadastral_records = _index_records(options.get("cadastralCharacteristics") or options.get("cadastral_characteristics") or [])
        official_price_records = _index_records(options.get("officialLandPrices") or options.get("official_land_prices") or [])
        registry_records = _index_records(options.get("registryEvidence") or options.get("registry_evidence") or [])

        parcels = analysis.get("parcels", [])
        parcel_results = []
        for parcel in parcels:
            parcel_results.append(
                self._enrich_parcel(
                    parcel,
                    legal_dong_code=legal_dong_code,
                    pnu_records=pnu_records,
                    cadastral_records=cadastral_records,
                    official_price_records=official_price_records,
                    registry_records=registry_records,
                )
            )

        completed_at = _now()
        status_counts: Dict[str, int] = {}
        for result in parcel_results:
            status_counts[result["status"]] = status_counts.get(result["status"], 0) + 1

        blocking = [
            issue
            for result in parcel_results
            for issue in result["issues"]
            if issue["severity"] in {"blocking", "blocking_for_enrichment", "blocking_for_pf_review"}
        ]
        analysis["enrichmentRun"] = {
            "agentVersion": LAND_LEDGER_ENRICHMENT_AGENT_VERSION,
            "startedAt": started_at,
            "completedAt": completed_at,
            "mode": options.get("mode", "manual_or_configured_connector"),
            "legalDongCode": legal_dong_code,
            "confidenceThreshold": DEFAULT_PNU_CONFIDENCE_THRESHOLD,
            "currentOfficialPriceBaseYear": CURRENT_OFFICIAL_PRICE_BASE_YEAR,
            "parcelResults": parcel_results,
            "statusCounts": status_counts,
            "status": "completed" if not blocking else "partial_success",
            "blockingIssueCount": len(blocking),
            "dataSourcePolicy": {
                "pnu": "manual fixture, legal-dong-code resolver, or future public API adapter",
                "cadastral": "manual fixture or future cadastral/public land-use adapter",
                "officialLandPrice": "manual fixture or future official land price adapter; no price is fabricated",
                "registryEvidence": "manual evidence attachment or licensed registry source; no registry completion without evidence",
            },
        }
        return analysis

    def _enrich_parcel(
        self,
        parcel: Dict[str, Any],
        *,
        legal_dong_code: Optional[str],
        pnu_records: Dict[str, Dict[str, Any]],
        cadastral_records: Dict[str, Dict[str, Any]],
        official_price_records: Dict[str, Dict[str, Any]],
        registry_records: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        result = {
            "sequence": parcel.get("sequence"),
            "addressFragment": parcel.get("addressFragment"),
            "pnuResolved": False,
            "cadastralAttached": False,
            "officialLandPriceAttached": False,
            "registryEvidenceAttached": False,
            "status": "enrichment_failed",
            "issues": [],
        }

        pnu_record = _lookup_record(pnu_records, parcel)
        if pnu_record:
            self._apply_pnu_record(parcel, pnu_record)
            result["pnuResolved"] = True
        elif legal_dong_code:
            generated = _generate_pnu(parcel, legal_dong_code)
            if generated:
                self._apply_pnu_record(parcel, generated)
                result["pnuResolved"] = True
            else:
                result["issues"].append(_issue("pnu_generation_failed", "blocking_for_enrichment", "PNU could not be generated from legal dong and lot number."))
        elif not parcel.get("pnu"):
            result["issues"].append(_issue("pnu_resolver_unconfigured", "blocking_for_enrichment", "No PNU record or legal dong code was provided."))

        cadastral_record = _lookup_record(cadastral_records, parcel)
        if cadastral_record:
            self._apply_cadastral_record(parcel, cadastral_record)
            result["cadastralAttached"] = True
        elif parcel.get("pnu") and not parcel.get("cadastralCharacteristics"):
            result["issues"].append(_issue("cadastral_characteristics_missing", "caution", "Cadastral characteristics were not provided for the resolved PNU."))

        official_price_record = _lookup_record(official_price_records, parcel)
        if official_price_record:
            self._apply_official_price_record(parcel, official_price_record)
            result["officialLandPriceAttached"] = True
        elif parcel.get("pnu") and not parcel.get("officialLandPricePerSqm"):
            result["issues"].append(_issue("official_land_price_connector_required", "blocking_for_enrichment", "Official land price was not provided and must be fetched with base year/source evidence."))

        registry_record = _lookup_record(registry_records, parcel)
        if registry_record:
            self._apply_registry_record(parcel, registry_record)
            result["registryEvidenceAttached"] = True
        elif parcel.get("pnu") and not parcel.get("registryEvidence"):
            result["issues"].append(_issue("registry_evidence_required", "blocking_for_pf_review", "Registry/encumbrance evidence is required before legal or PF review completion."))

        if result["issues"]:
            result["status"] = "partial_success" if result["pnuResolved"] else "needs_connector_input"
        else:
            result["status"] = "enriched"
        return result

    def _apply_pnu_record(self, parcel: Dict[str, Any], record: Dict[str, Any]) -> None:
        pnu = record.get("pnu") or record.get("resolvedPnu")
        confidence = record.get("matchConfidence") or record.get("confidence") or record.get("pnuResolveConfidence")
        parcel["pnu"] = pnu
        parcel["resolvedPnu"] = pnu
        parcel["resolvedLegalAddress"] = record.get("resolvedLegalAddress") or record.get("legalAddress") or parcel.get("addressFragment")
        parcel["pnuMatchMethod"] = record.get("matchMethod") or record.get("pnuMatchMethod") or "manual_or_legal_dong_lot_match"
        parcel["pnuCandidateCount"] = record.get("candidateCount") or record.get("pnuCandidateCount") or 1
        parcel["pnuResolverWarning"] = record.get("resolverWarning") or record.get("pnuResolverWarning")
        parcel["pnuResolveConfidence"] = confidence
        parcel.setdefault("enrichmentEvidence", {})
        parcel["enrichmentEvidence"]["pnuResolution"] = {
            "sourceName": record.get("sourceName", "LandLedgerEnrichmentAgent"),
            "sourceType": record.get("sourceType", "manual_or_configured_connector"),
            "sourceUrlOrEvidenceId": record.get("sourceUrlOrEvidenceId") or record.get("evidenceId"),
            "baseDate": record.get("baseDate"),
            "fetchedAt": record.get("fetchedAt") or _now(),
            "confidence": confidence,
        }

    def _apply_cadastral_record(self, parcel: Dict[str, Any], record: Dict[str, Any]) -> None:
        parcel["cadastralCharacteristics"] = {
            "pnu": record.get("pnu") or parcel.get("pnu"),
            "landCategory": record.get("landCategory") or parcel.get("landCategory"),
            "standardLandCategoryCode": record.get("standardLandCategoryCode") or parcel.get("standardLandCategoryCode"),
            "areaSqm": record.get("areaSqm") or parcel.get("areaSqm"),
            "zoning": record.get("zoning"),
            "landUseZones": record.get("landUseZones", []),
            "sourceName": record.get("sourceName"),
            "sourceUrlOrEvidenceId": record.get("sourceUrlOrEvidenceId") or record.get("evidenceId"),
            "baseDate": record.get("baseDate"),
            "fetchedAt": record.get("fetchedAt") or _now(),
        }
        parcel.setdefault("evidence", []).append(_evidence("cadastral_record", record, parcel))

    def _apply_official_price_record(self, parcel: Dict[str, Any], record: Dict[str, Any]) -> None:
        price_per_sqm = record.get("pricePerSqm") or record.get("officialLandPricePerSqm")
        base_year = record.get("baseYear") or record.get("officialLandPriceBaseYear")
        base_date = record.get("baseDate") or record.get("officialLandPriceBaseDate")
        parcel["officialLandPricePerSqm"] = price_per_sqm
        parcel["officialLandPriceBaseYear"] = base_year
        parcel["officialLandPriceBaseDate"] = base_date
        parcel["officialTotalLandValue"] = record.get("officialTotalLandValue") or (
            (price_per_sqm or 0) * (parcel.get("areaSqm") or 0) if price_per_sqm else None
        )
        parcel["officialLandPriceSource"] = record.get("sourceName") or "manual_or_configured_connector"
        parcel["officialLandPriceSourceUrlOrEvidenceId"] = record.get("sourceUrlOrEvidenceId") or record.get("evidenceId")
        parcel["officialLandPriceFetchedAt"] = record.get("fetchedAt") or _now()
        parcel.setdefault("evidence", []).append(_evidence("official_land_price", record, parcel))

    def _apply_registry_record(self, parcel: Dict[str, Any], record: Dict[str, Any]) -> None:
        parcel["registryEvidence"] = {
            "evidenceId": record.get("evidenceId") or _default_evidence_id("registry_record", parcel),
            "source": record.get("sourceName") or record.get("source") or "manual_registry_evidence",
            "sourceUrlOrEvidenceId": record.get("sourceUrlOrEvidenceId") or record.get("evidenceId"),
            "issuedAt": record.get("issuedAt"),
            "fetchedAt": record.get("fetchedAt") or _now(),
            "registeredOwner": record.get("registeredOwner"),
            "ownerMatchStatus": record.get("ownerMatchStatus", "not_verified"),
            "ownershipShare": record.get("ownershipShare"),
            "mortgageExists": bool(record.get("mortgageExists", False)),
            "seizureExists": bool(record.get("seizureExists", False)),
            "provisionalRegistrationExists": bool(record.get("provisionalRegistrationExists", False)),
            "easementExists": bool(record.get("easementExists", False)),
            "trustRegistrationExists": bool(record.get("trustRegistrationExists", False)),
            "restrictionSummary": record.get("restrictionSummary", ""),
            "releaseRequired": bool(record.get("releaseRequired", False)),
            "releaseCondition": record.get("releaseCondition"),
            "verificationStatus": record.get("verificationStatus", "attached_pending_legal_review"),
        }
        parcel.setdefault("evidence", []).append(_evidence("registry_record", record, parcel))


def _generate_pnu(parcel: Dict[str, Any], legal_dong_code: str) -> Optional[Dict[str, Any]]:
    if not (legal_dong_code and len(str(legal_dong_code)) == 10 and str(legal_dong_code).isdigit()):
        return None
    main = parcel.get("mainLotNo")
    sub = parcel.get("subLotNo", 0)
    if main is None:
        return None
    mountain_flag = "2" if parcel.get("isMountain") else "1"
    pnu = f"{legal_dong_code}{mountain_flag}{int(main):04d}{int(sub or 0):04d}"
    return {
        "pnu": pnu,
        "resolvedLegalAddress": parcel.get("addressFragment"),
        "matchConfidence": 0.95,
        "matchMethod": "legal_dong_code_and_normalized_lot",
        "candidateCount": 1,
        "sourceName": "configured_legal_dong_code",
        "sourceType": "manual_configuration",
    }


def _index_records(records: Any) -> Dict[str, Dict[str, Any]]:
    indexed: Dict[str, Dict[str, Any]] = {}
    if isinstance(records, dict):
        iterable = records.values()
    elif isinstance(records, list):
        iterable = records
    else:
        iterable = []
    for record in iterable:
        if not isinstance(record, dict):
            continue
        for key in _record_keys(record):
            indexed[key] = record
    return indexed


def _record_keys(record: Dict[str, Any]) -> List[str]:
    keys = []
    for field in ["pnu", "resolvedPnu", "addressFragment", "normalizedLotNumber", "parcelKey", "sequence"]:
        value = record.get(field)
        if value not in (None, ""):
            keys.append(str(value))
    return keys


def _lookup_record(records: Dict[str, Dict[str, Any]], parcel: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    candidates = [
        parcel.get("pnu"),
        parcel.get("resolvedPnu"),
        parcel.get("addressFragment"),
        parcel.get("normalizedLotNumber"),
        parcel.get("sequence"),
    ]
    for candidate in candidates:
        if candidate in (None, ""):
            continue
        record = records.get(str(candidate))
        if record:
            return record
    return None


def _evidence(evidence_type: str, record: Dict[str, Any], parcel: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "evidenceId": record.get("evidenceId") or _default_evidence_id(evidence_type, parcel),
        "evidenceType": evidence_type,
        "source": record.get("sourceName") or record.get("source") or "manual_or_configured_connector",
        "sourceUrlOrEvidenceId": record.get("sourceUrlOrEvidenceId") or record.get("evidenceId"),
        "issuedAt": record.get("issuedAt"),
        "fetchedAt": record.get("fetchedAt") or _now(),
        "verificationStatus": record.get("verificationStatus", "attached_pending_review"),
    }


def _default_evidence_id(evidence_type: str, parcel: Dict[str, Any]) -> str:
    key = parcel.get("pnu") or parcel.get("addressFragment") or parcel.get("sequence")
    return f"{evidence_type}:{key}"


def _issue(code: str, severity: str, message: str) -> Dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
