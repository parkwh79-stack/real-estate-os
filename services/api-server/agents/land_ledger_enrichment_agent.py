from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agents.land_ledger_agent import CURRENT_OFFICIAL_PRICE_BASE_YEAR, DEFAULT_PNU_CONFIDENCE_THRESHOLD
from integrations.land_data_connectors import LandDataConnectorRouter
from use_cases.parcel_pnu_resolution import parcel_pnu


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
        live_connector_result: Dict[str, Any] = {}
        parcels = analysis.get("parcels", [])
        if options.get("useLiveConnectors") or options.get("use_live_connectors"):
            live_connector_result = LandDataConnectorRouter().fetch_records_for_parcels(
                [{**parcel, "pnu": pnu} for parcel in parcels if (pnu := parcel_pnu(parcel))],
                base_year=options.get("baseYear") or options.get("base_year") or CURRENT_OFFICIAL_PRICE_BASE_YEAR,
                timeout_seconds=float(options.get("connectorTimeoutSeconds") or options.get("connector_timeout_seconds") or 8.0),
            )
            options = _merge_connector_result(options, live_connector_result)

        pnu_records = _index_records(options.get("pnuResolutions") or options.get("pnu_resolutions") or [])
        cadastral_records = _index_records(options.get("cadastralCharacteristics") or options.get("cadastral_characteristics") or [])
        land_use_records = _index_records(options.get("landUsePlanningRecords") or options.get("land_use_planning_records") or [])
        building_use_records = _index_records(options.get("buildingUseRecords") or options.get("building_use_records") or [])
        land_characteristics_records = _index_records(options.get("landCharacteristicsRecords") or options.get("land_characteristics_records") or [])
        official_price_records = _index_records(options.get("officialLandPrices") or options.get("official_land_prices") or [])
        registry_records = _index_records(options.get("registryEvidence") or options.get("registry_evidence") or [])

        parcel_results = []
        for parcel in parcels:
            parcel_results.append(
                self._enrich_parcel(
                    parcel,
                    legal_dong_code=legal_dong_code,
                    pnu_records=pnu_records,
                    cadastral_records=cadastral_records,
                    land_use_records=land_use_records,
                    building_use_records=building_use_records,
                    land_characteristics_records=land_characteristics_records,
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
            "liveConnectorResult": live_connector_result or {
                "schemaVersion": "realytics-land-data-connectors-v1",
                "mode": "not_requested",
                "diagnostics": [],
            },
            "dataSourcePolicy": {
                "pnu": "manual fixture, legal-dong-code resolver, or future public API adapter",
                "cadastral": "manual fixture or configured cadastral/public land-use adapter",
                "landCharacteristics": "VWorld getLandCharacteristics source-backed parcel area, land category, use districts, terrain, road frontage, and official land price for the requested base year; values require reconciliation with the ledger and other evidence",
                "buildingUse": "VWorld getBuildingUse source-backed building attributes; existing building facts require reconciliation with the proposed program",
                "officialLandPrice": "manual fixture or configured official land price adapter; no price is fabricated",
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
        land_use_records: Dict[str, Dict[str, Any]],
        building_use_records: Dict[str, Dict[str, Any]],
        land_characteristics_records: Dict[str, Dict[str, Any]],
        official_price_records: Dict[str, Dict[str, Any]],
        registry_records: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        result = {
            "sequence": parcel.get("sequence"),
            "addressFragment": parcel.get("addressFragment"),
            "pnuResolved": False,
            "cadastralAttached": False,
            "landUsePlanningAttached": False,
            "buildingUseAttached": False,
            "landCharacteristicsAttached": False,
            "officialLandPriceAttached": False,
            "registryEvidenceAttached": False,
            "status": "enrichment_failed",
            "issues": [],
        }

        pnu_record = _lookup_record(pnu_records, parcel)
        if pnu_record and pnu_record.get("matchMethod") != "legal_dong_code_and_normalized_lot":
            self._apply_pnu_record(parcel, pnu_record)
            result["pnuResolved"] = True
        elif parcel_pnu(parcel):
            result["pnuResolved"] = True
        elif legal_dong_code:
            generated = _generate_pnu(parcel, legal_dong_code)
            if generated:
                parcel["pnuCandidate"] = generated["pnu"]
                result["issues"].append(_issue("pnu_candidate_unverified", "blocking_for_enrichment", "A legal dong code and lot form a candidate PNU; verify the cadastral parcel before attaching evidence."))
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

        land_use_record = _lookup_record(land_use_records, parcel)
        if land_use_record:
            self._apply_land_use_record(parcel, land_use_record)
            result["landUsePlanningAttached"] = True

        building_use_record = _lookup_record(building_use_records, parcel)
        if building_use_record:
            self._apply_building_use_record(parcel, building_use_record)
            result["buildingUseAttached"] = True

        land_characteristics_record = _lookup_record(land_characteristics_records, parcel)
        if land_characteristics_record:
            self._apply_land_characteristics_record(parcel, land_characteristics_record)
            result["landCharacteristicsAttached"] = True

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
        parcel.pop("pnuCandidate", None)
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
            "fetchedAt": record.get("fetchedAt"),
            "recordedAt": _now(),
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
            "fetchedAt": record.get("fetchedAt"),
            "recordedAt": _now(),
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
        parcel["officialLandPriceFetchedAt"] = record.get("fetchedAt")
        parcel.setdefault("evidence", []).append(_evidence("official_land_price", record, parcel))

    def _apply_land_use_record(self, parcel: Dict[str, Any], record: Dict[str, Any]) -> None:
        evidence = _evidence("land_use_planning", record, parcel)
        parcel["landUsePlanning"] = {
            "pnu": record.get("pnu") or parcel.get("pnu"),
            "zoning": record.get("zoning"),
            "landUseZones": record.get("landUseZones", []),
            "legalFar": record.get("legalFar"),
            "legalBcr": record.get("legalBcr"),
            "districtUnitPlan": record.get("districtUnitPlan"),
            "landUsePlanItems": record.get("landUsePlanItems", []),
            "coverage": record.get("coverage"),
            "missingFields": record.get("missingFields", []),
            "sourceName": record.get("sourceName"),
            "sourceUrlOrEvidenceId": record.get("sourceUrlOrEvidenceId") or record.get("evidenceId"),
            "sourceLocator": record.get("sourceLocator") or {
                "sourceType": "land_use_planning_record",
                "evidenceId": evidence["evidenceId"],
                "apiRowId": record.get("pnu"),
            },
            "baseDate": record.get("baseDate"),
            "fetchedAt": record.get("fetchedAt"),
            "recordedAt": _now(),
        }
        parcel.setdefault("evidence", []).append(evidence)

    def _apply_building_use_record(self, parcel: Dict[str, Any], record: Dict[str, Any]) -> None:
        parcel.setdefault("buildingUseRecords", []).append({
            key: value for key, value in record.items()
            if key not in {"pnu", "fetchedAt"}
        } | {
            "pnu": record.get("pnu") or parcel.get("pnu"),
            "fetchedAt": record.get("fetchedAt"),
            "recordedAt": _now(),
        })
        parcel.setdefault("evidence", []).append(_evidence("building_use", record, parcel))

    def _apply_land_characteristics_record(self, parcel: Dict[str, Any], record: Dict[str, Any]) -> None:
        parcel["landCharacteristics"] = dict(record)
        parcel["landCharacteristicsSource"] = record.get("sourceName")
        parcel["landCharacteristicsSourceUrlOrEvidenceId"] = record.get("sourceUrlOrEvidenceId")
        # VWorld returns the official land price in this endpoint as well. Use
        # it only as a source-backed fallback; a dedicated official-price
        # record applied later remains authoritative when present.
        if not parcel.get("officialLandPricePerSqm") and record.get("officialLandPricePerSqm") is not None:
            parcel["officialLandPricePerSqm"] = record["officialLandPricePerSqm"]
            parcel["officialLandPriceBaseYear"] = record.get("standardYear")
            parcel["officialLandPriceBaseDate"] = record.get("baseDate")
            parcel["officialTotalLandValue"] = (
                record["officialLandPricePerSqm"] * record["areaSqm"]
                if record.get("areaSqm") is not None else None
            )
            parcel["officialLandPriceSource"] = record.get("sourceName")
            parcel["officialLandPriceSourceUrlOrEvidenceId"] = record.get("sourceUrlOrEvidenceId")
        parcel.setdefault("evidence", []).append(_evidence("land_characteristics", record, parcel))

    def _apply_registry_record(self, parcel: Dict[str, Any], record: Dict[str, Any]) -> None:
        parcel["registryEvidence"] = {
            "evidenceId": record.get("evidenceId") or _default_evidence_id("registry_record", parcel),
            "source": record.get("sourceName") or record.get("source") or "manual_registry_evidence",
            "sourceUrlOrEvidenceId": record.get("sourceUrlOrEvidenceId") or record.get("evidenceId"),
            "issuedAt": record.get("issuedAt"),
            "fetchedAt": record.get("fetchedAt"),
            "recordedAt": _now(),
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
    parcel_pnus = {str(parcel[key]) for key in ("pnu", "resolvedPnu") if parcel.get(key)}
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
            record_pnus = {str(record[key]) for key in ("pnu", "resolvedPnu") if record.get(key)}
            if parcel_pnus and record_pnus and parcel_pnus.isdisjoint(record_pnus):
                # Row numbers and address fragments are only fallback matches;
                # they must never override contradictory parcel identifiers.
                continue
            return record
    return None


def _merge_connector_result(options: Dict[str, Any], connector_result: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(options)
    for key in ["pnuResolutions", "cadastralCharacteristics", "landUsePlanningRecords", "buildingUseRecords", "landCharacteristicsRecords", "regionLandPriceTrends", "officialLandPrices", "registryEvidence"]:
        existing = merged.get(key) or []
        if not isinstance(existing, list):
            existing = list(existing.values()) if isinstance(existing, dict) else []
        fetched = connector_result.get(key) or []
        merged[key] = existing + [item for item in fetched if isinstance(item, dict)]
    return merged


def _evidence(evidence_type: str, record: Dict[str, Any], parcel: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "evidenceId": record.get("evidenceId") or _default_evidence_id(evidence_type, parcel),
        "evidenceType": evidence_type,
        "source": record.get("sourceName") or record.get("source") or "manual_or_configured_connector",
        "sourceUrlOrEvidenceId": record.get("sourceUrlOrEvidenceId") or record.get("evidenceId"),
        "issuedAt": record.get("issuedAt"),
        "fetchedAt": record.get("fetchedAt"),
        "recordedAt": _now(),
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
