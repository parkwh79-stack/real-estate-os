from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from time import monotonic
from collections.abc import Callable
from typing import Any, Dict, List, Optional

from integrations.vworld import fetch_parcel_by_pnu


CONNECTOR_ROUTER_VERSION = "realytics-land-data-connectors-v1"
VWORLD_LAND_USE_ATTR_URL = "https://api.vworld.kr/ned/data/getLandUseAttr"
VWORLD_BUILDING_USE_URL = "https://api.vworld.kr/ned/data/getBuildingUse"
VWORLD_REGION_LAND_PRICE_CHANGE_URL = "https://api.vworld.kr/ned/data/getByRegion"
VWORLD_LAND_CHARACTERISTICS_URL = "https://api.vworld.kr/ned/data/getLandCharacteristics"


class LandDataConnectorRouter:
    """Fetch parcel enrichment records from configured external connectors.

    The router is intentionally conservative. It only calls a connector when an
    API key is configured (or an approved URL template is supplied), and every
    returned record carries source metadata. Missing connector configuration is
    reported as a diagnostic instead of silently manufacturing official data.
    """

    def __init__(self, environ: Optional[Dict[str, str]] = None) -> None:
        self.environ = environ or os.environ

    def fetch_records_for_parcels(
        self,
        parcels: List[Dict[str, Any]],
        *,
        legal_dong_code: str | None = None,
        base_year: int | str | None = None,
        trend_month: int | str | None = None,
        trend_scope: str | None = None,
        include_region_trends: bool = True,
        timeout_seconds: float = 8.0,
        evidence_types: set[str] | None = None,
        deadline: float | None = None,
    ) -> Dict[str, Any]:
        supported = {"cadastral", "land_use_planning", "building_use", "land_characteristics",
                     "official_land_price", "region_land_price_trend"}
        if evidence_types is not None and not evidence_types <= supported:
            raise ValueError("Unsupported land evidence type")

        def fetch(kind: str, execute: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
            empty: Dict[str, Any] = {"record": None, "records": [], "diagnostics": []}
            if evidence_types is not None and kind not in evidence_types:
                return empty
            if deadline is not None and monotonic() >= deadline:
                empty["diagnostics"] = [_diagnostic(
                    connector=kind, status="deferred", message="Public lookup time budget exhausted.", parcel={},
                )]
                return empty
            return execute()

        def timeout() -> float:
            return timeout_seconds if deadline is None else max(0.01, min(timeout_seconds, deadline - monotonic()))

        diagnostics: List[Dict[str, Any]] = []
        pnu_resolutions: List[Dict[str, Any]] = []
        cadastral_records: List[Dict[str, Any]] = []
        official_price_records: List[Dict[str, Any]] = []
        land_use_planning_records: List[Dict[str, Any]] = []
        building_use_records: List[Dict[str, Any]] = []
        land_characteristics_records: List[Dict[str, Any]] = []
        region_land_price_trends: List[Dict[str, Any]] = []

        region_code = legal_dong_code or next(
            (str(parcel.get("pnu") or parcel.get("resolvedPnu"))[:10]
             for parcel in parcels if str(parcel.get("pnu") or parcel.get("resolvedPnu") or "").isdigit()),
            None,
        )
        if region_code and include_region_trends:
            trend = fetch("region_land_price_trend", lambda: self._fetch_region_land_price_change(
                region_code,
                standard_year=base_year,
                standard_month=trend_month,
                scope_div=trend_scope,
                timeout_seconds=timeout(),
            ))
            region_land_price_trends.extend(trend.get("records", []))
            diagnostics.extend(trend.get("diagnostics", []))

        for parcel in parcels:
            pnu = parcel.get("pnu") or parcel.get("resolvedPnu")
            if not pnu and legal_dong_code:
                generated = generate_pnu_from_parcel(parcel, legal_dong_code)
                if generated:
                    pnu = generated["pnu"]
                    pnu_resolutions.append(generated)

            if pnu and not any(item.get("pnu") == pnu for item in pnu_resolutions):
                pnu_resolutions.append({
                    "pnu": str(pnu),
                    "matchMethod": parcel.get("pnuMatchMethod") or "supplied_or_prior_resolution",
                    "matchConfidence": parcel.get("pnuResolveConfidence") or 1.0,
                    "sourceLocator": parcel.get("sourceLocator") or parcel.get("source_locator"),
                })

            if not pnu:
                diagnostics.append(_diagnostic(
                    connector="pnu",
                    status="skipped_missing_pnu",
                    message="PNU could not be generated because legal dong code or lot fields are missing.",
                    parcel=parcel,
                ))
                continue

            land_characteristics = fetch("land_characteristics", lambda: self._fetch_land_characteristics_record(
                pnu,
                standard_year=base_year,
                parcel=parcel,
                timeout_seconds=timeout(),
            ))
            if land_characteristics.get("record"):
                land_characteristics_records.append(land_characteristics["record"])
            diagnostics.extend(land_characteristics["diagnostics"])

            cadastral_record = fetch("cadastral", lambda: self._fetch_cadastral_record(
                pnu,
                parcel=parcel,
                timeout_seconds=timeout(),
            ))
            if cadastral_record.get("record"):
                cadastral_records.append(cadastral_record["record"])
            diagnostics.extend(cadastral_record["diagnostics"])

            land_use_record = fetch("land_use_planning", lambda: self._fetch_land_use_planning_record(
                pnu,
                parcel=parcel,
                cadastral_record=cadastral_record.get("record"),
                timeout_seconds=timeout(),
            ))
            if land_use_record.get("record"):
                land_use_planning_records.append(land_use_record["record"])
            diagnostics.extend(land_use_record["diagnostics"])

            building_use = fetch("building_use", lambda: self._fetch_building_use_records(
                pnu,
                parcel=parcel,
                timeout_seconds=timeout(),
            ))
            building_use_records.extend(building_use.get("records", []))
            diagnostics.extend(building_use["diagnostics"])

            official_price_record = fetch("official_land_price", lambda: self._fetch_official_land_price(
                pnu,
                base_year=base_year,
                parcel=parcel,
                timeout_seconds=timeout(),
            ))
            if official_price_record.get("record"):
                official_price_records.append(official_price_record["record"])
            diagnostics.extend(official_price_record["diagnostics"])

        return {
            "schemaVersion": CONNECTOR_ROUTER_VERSION,
            "generatedAt": _now(),
            "mode": "live_connector_router",
            "status": "completed" if not diagnostics else "partial",
            "pnuResolutions": pnu_resolutions,
            "cadastralCharacteristics": cadastral_records,
            "landUsePlanningRecords": land_use_planning_records,
            "buildingUseRecords": building_use_records,
            "landCharacteristicsRecords": land_characteristics_records,
            "regionLandPriceTrends": region_land_price_trends,
            "officialLandPrices": official_price_records,
            "registryEvidence": [],
            "diagnostics": diagnostics,
            "dataSourcePolicy": {
                "pnu": "Generated only from confirmed legal dong code and normalized lot fields, or supplied by connector records.",
                "cadastral": "Fetched only when REALYTICS_CADASTRAL_URL_TEMPLATE and API key are configured.",
                "landUsePlanning": "VWorld getLandUseAttr stores source-returned planning designations and conflict status. Statutory FAR/BCR and the primary use district require authoritative source fields or separate evidence.",
                "buildingUse": "VWorld getBuildingUse stores source-returned building use, area, floor, permit, and approval fields; no values are inferred when a PNU has no response.",
                "landCharacteristics": "VWorld getLandCharacteristics stores source-returned parcel area, land category, use district, terrain, road 접면, and official land price fields for the requested base year.",
                "regionLandPriceTrend": "VWorld getByRegion stores source-returned monthly and cumulative land price change rates for the requested administrative scope; it is not a parcel valuation.",
                "officialLandPrice": "Fetched only when REALYTICS_OFFICIAL_LAND_PRICE_URL_TEMPLATE and API key are configured.",
                "registryEvidence": "Live registry lookup is not implemented; attach licensed registry evidence manually.",
            },
        }

    def _fetch_cadastral_record(
        self,
        pnu: str,
        *,
        parcel: Dict[str, Any],
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        template = self.environ.get("REALYTICS_CADASTRAL_URL_TEMPLATE")
        api_key = _first_env(self.environ, ["REALYTICS_VWORLD_API_KEY", "VWORLD_API_KEY", "REALYTICS_PUBLIC_DATA_API_KEY", "DATA_GO_KR_API_KEY"])
        if not template:
            if api_key:
                properties, _diagnostic_result = fetch_parcel_by_pnu(
                    pnu,
                    environ=self.environ,
                    timeout=timeout_seconds,
                )
                if properties and _response_matches_pnu(properties, pnu):
                    source_id = f"vworld:{pnu}:LP_PA_CBND_BUBUN"
                    return {
                        "record": {
                            "pnu": pnu,
                            "sourcePnuVerified": True,
                            "cadastralAddress": _pick(properties, ["addr", "cadastralAddress"]),
                            "landCategory": _pick(properties, ["landCategory", "lndcgrCodeNm", "landCategoryName"]),
                            "standardLandCategoryCode": _pick(properties, ["standardLandCategoryCode", "lndcgrCode"]),
                            "areaSqm": _to_float(_pick(properties, ["areaSqm", "lndpclAr", "area"])),
                            "zoning": _pick(properties, ["zoning", "prposArea1Nm", "prposArea1", "useDistrict"]),
                            "landUseZones": _as_list(_pick(properties, ["landUseZones", "landUse", "prposArea2Nm"])),
                            "sourceName": "VWorld cadastral properties",
                            "sourceUrlOrEvidenceId": source_id,
                            "baseDate": _pick(properties, ["baseDate", "stdrYm"]),
                            "fetchedAt": _now(),
                        },
                        "diagnostics": [_diagnostic(
                            connector="cadastral",
                            status="fetched",
                            message="VWorld cadastral properties returned a source-backed record.",
                            parcel=parcel,
                            url=source_id,
                        )],
                    }
            return {"record": None, "diagnostics": [_diagnostic(
                connector="cadastral",
                status="skipped_missing_config",
                message="REALYTICS_CADASTRAL_URL_TEMPLATE is not configured.",
                parcel=parcel,
            )]}
        if "{service_key}" in template and not api_key:
            return {"record": None, "diagnostics": [_diagnostic(
                connector="cadastral",
                status="skipped_missing_api_key",
                message="Cadastral connector requires REALYTICS_VWORLD_API_KEY, VWORLD_API_KEY, REALYTICS_PUBLIC_DATA_API_KEY, or DATA_GO_KR_API_KEY.",
                parcel=parcel,
            )]}

        response = self._fetch_template(
            template,
            pnu=pnu,
            service_key=api_key,
            base_year=None,
            timeout_seconds=timeout_seconds,
        )
        if response.get("error"):
            return {"record": None, "diagnostics": [_diagnostic(
                connector="cadastral",
                status="fetch_failed",
                message=response["error"],
                parcel=parcel,
                url=response.get("url"),
            )]}

        data = _parse_payload(response.get("body", ""))
        items = _payload_items(data)
        item = next((row for row in items if _response_matches_pnu(row, pnu)), None)
        if not item:
            return {"record": None, "diagnostics": [_diagnostic(
                connector="cadastral",
                status="pnu_unverified" if items else "no_record",
                message="Cadastral connector returned no row with the requested PNU.",
                parcel=parcel,
                url=response.get("url"),
            )]}

        return {
            "record": {
                "pnu": pnu,
                "sourcePnuVerified": True,
                "cadastralAddress": _pick(item, ["addr", "cadastralAddress", "address"]),
                "landCategory": _pick(item, ["landCategory", "lndcgrCodeNm", "지목", "lndcgrCode"]),
                "standardLandCategoryCode": _pick(item, ["standardLandCategoryCode", "lndcgrCode", "지목코드"]),
                "areaSqm": _to_float(_pick(item, ["areaSqm", "lndpclAr", "pblntfPclnd", "면적"])),
                "zoning": _pick(item, ["zoning", "prposArea1Nm", "용도지역"]),
                "landUseZones": _as_list(_pick(item, ["landUseZones", "landUse", "지역지구"])),
                "sourceName": self.environ.get("REALYTICS_CADASTRAL_SOURCE_NAME", "configured_cadastral_connector"),
                "sourceUrlOrEvidenceId": response.get("url"),
                "baseDate": _pick(item, ["baseDate", "stdrYm", "기준일"]),
                "fetchedAt": _now(),
            },
            "diagnostics": [_diagnostic(
                connector="cadastral",
                status="fetched",
                message="Cadastral connector returned a source-backed record.",
                parcel=parcel,
                url=response.get("url"),
            )],
        }

    def _fetch_land_use_planning_record(
        self,
        pnu: str,
        *,
        parcel: Dict[str, Any],
        cadastral_record: Dict[str, Any] | None,
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        """Fetch source-backed land-use planning fields without inference.

        Public land-use endpoints differ by service and key, so the endpoint is
        configured per deployment. A VWorld cadastral response may provide a
        zoning context, but statutory FAR/BCR are never derived from a zoning
        name alone.
        """
        template = (
            self.environ.get("REALYTICS_LAND_USE_URL_TEMPLATE")
            or self.environ.get("REALYTICS_VWORLD_LAND_USE_URL_TEMPLATE")
        )
        api_key = _first_env(self.environ, [
            "REALYTICS_LAND_USE_API_KEY",
            "REALYTICS_PUBLIC_DATA_API_KEY",
            "DATA_GO_KR_API_KEY",
            "REALYTICS_VWORLD_API_KEY",
            "VWORLD_API_KEY",
        ])

        if not template:
            vworld_key = _first_env(self.environ, ["REALYTICS_VWORLD_API_KEY", "VWORLD_API_KEY"])
            lookup_failed = False
            if vworld_key:
                response = self._fetch_vworld_land_use_attr(
                    pnu,
                    service_key=vworld_key,
                    timeout_seconds=timeout_seconds,
                )
                lookup_failed = bool(response.get("error"))
                if not response.get("error"):
                    items = _payload_items(_parse_payload(response.get("body", "")))
                    record = self._normalize_land_use_record(
                        pnu,
                        items,
                        source_name="VWorld land-use planning attributes",
                        source_url=response.get("url"),
                    )
                    if record:
                        return {
                            "record": record,
                            "diagnostics": [_diagnostic(
                                connector="land_use_planning",
                                status="fetched",
                                message="VWorld land-use planning attributes returned source-backed designations.",
                                parcel=parcel,
                                url=response.get("url"),
                            )],
                        }
            zoning = _pick(cadastral_record or {}, ["zoning", "useDistrict"])
            if zoning:
                source_id = (cadastral_record or {}).get("sourceUrlOrEvidenceId") or f"vworld:{pnu}:LP_PA_CBND_BUBUN"
                return {
                    "record": {
                        "pnu": pnu,
                        "zoning": zoning,
                        "landUseZones": _as_list((cadastral_record or {}).get("landUseZones")),
                        "legalFar": None,
                        "legalBcr": None,
                        "districtUnitPlan": None,
                        "coverage": "zoning_only",
                        "sourcePnuVerified": True,
                        "missingFields": ["legalFar", "legalBcr", "districtUnitPlan"],
                        "sourceName": (cadastral_record or {}).get("sourceName") or "VWorld cadastral properties",
                        "sourceType": "public_api",
                        "sourceUrlOrEvidenceId": source_id,
                        "fetchedAt": _now(),
                    },
                    "diagnostics": [_diagnostic(
                        connector="land_use_planning",
                        status="partial_fetched",
                        message="VWorld provided zoning context; statutory FAR/BCR require a configured land-use planning endpoint.",
                        parcel=parcel,
                        url=source_id,
                    )],
                }
            return {"record": None, "diagnostics": [_diagnostic(
                connector="land_use_planning",
                status="fetch_failed" if lookup_failed else "no_record" if vworld_key else "skipped_missing_config",
                message=(
                    "VWorld land-use planning attributes returned no usable designation; "
                    "statutory FAR/BCR were not inferred."
                    if vworld_key
                    else "VWorld API key and REALYTICS_LAND_USE_URL_TEMPLATE are not configured; "
                    "statutory FAR/BCR were not inferred."
                ),
                parcel=parcel,
            )]}
        if "{service_key}" in template and not api_key:
            return {"record": None, "diagnostics": [_diagnostic(
                connector="land_use_planning",
                status="skipped_missing_api_key",
                message="Land-use planning connector requires a configured public-data or VWorld API key.",
                parcel=parcel,
            )]}

        response = self._fetch_template(
            template,
            pnu=pnu,
            service_key=api_key,
            base_year=None,
            timeout_seconds=timeout_seconds,
        )
        if response.get("error"):
            return {"record": None, "diagnostics": [_diagnostic(
                connector="land_use_planning",
                status="fetch_failed",
                message=response["error"],
                parcel=parcel,
                url=response.get("url"),
            )]}

        items = _payload_items(_parse_payload(response.get("body", "")))
        record = self._normalize_land_use_record(
            pnu,
            items,
            source_name=self.environ.get("REALYTICS_LAND_USE_SOURCE_NAME", "configured_land_use_planning_connector"),
            source_url=response.get("url"),
        )
        if not record:
            return {"record": None, "diagnostics": [_diagnostic(
                connector="land_use_planning",
                status="pnu_unverified" if items and not any(_response_matches_pnu(row, pnu) for row in items) else "no_record",
                message="Land-use planning connector returned no usable row with the requested PNU.",
                parcel=parcel,
                url=response.get("url"),
            )]}

        return {
            "record": record,
            "diagnostics": [_diagnostic(
                connector="land_use_planning",
                status="fetched",
                message="Land-use planning response returned source-backed zoning and statutory fields.",
                parcel=parcel,
                url=response.get("url"),
            )],
        }

    def _fetch_vworld_land_use_attr(
        self,
        pnu: str,
        *,
        service_key: str,
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        params = {
            "pnu": pnu,
            "format": "json",
            "numOfRows": "1000",
            "pageNo": "1",
            "key": service_key,
        }
        domain = self.environ.get("VWORLD_DOMAIN") or self.environ.get("REALYTICS_VWORLD_DOMAIN")
        if domain:
            params["domain"] = domain
        url = f"{VWORLD_LAND_USE_ATTR_URL}?{urllib.parse.urlencode(params)}"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "REALYTICS/land-data-connectors-v1"})
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
            return {"url": _redact_service_key(url), "body": body}
        except Exception as exc:
            return {"url": _redact_service_key(url), "error": f"{exc.__class__.__name__}: {exc}"}

    def _normalize_land_use_record(
        self,
        pnu: str,
        items: List[Dict[str, Any]],
        *,
        source_name: str,
        source_url: str | None,
    ) -> Dict[str, Any] | None:
        normalized_items = [item for item in items if _response_matches_pnu(item, pnu)]
        if not normalized_items:
            return None

        explicit_zoning = _unique_strings(
            _pick(item, ["zoning", "zone", "useDistrict", "use_district"])
            for item in normalized_items
        )
        designations = _unique_strings(
            _pick(item, [
                "prposAreaDstrcCodeNm", "zoning", "zone", "useDistrict", "use_district",
                "prposArea1Nm", "landUseZone", "용도지역", "용도지역명",
            ])
            for item in normalized_items
        )
        far_values = {
            number for item in normalized_items
            if (number := _to_float(_pick(item, ["legalFar", "legal_far", "statutoryFar"]))) is not None
        }
        bcr_values = {
            number for item in normalized_items
            if (number := _to_float(_pick(item, ["legalBcr", "legal_bcr", "statutoryBcr"]))) is not None
        }
        district_plans = _unique_strings(
            _pick(item, ["districtUnitPlan", "districtUnitPlanName", "districtUnit", "지구단위계획"])
            for item in normalized_items
        )
        legal_far = next(iter(far_values)) if len(far_values) == 1 else None
        legal_bcr = next(iter(bcr_values)) if len(bcr_values) == 1 else None
        district_unit_plan = district_plans[0] if len(district_plans) == 1 else None
        if not designations and legal_far is None and legal_bcr is None and district_unit_plan is None:
            return None

        missing_fields = [
            key for key, value in {
                "zoning": explicit_zoning,
                "legalFar": legal_far,
                "legalBcr": legal_bcr,
                "districtUnitPlan": district_unit_plan,
            }.items() if value in (None, "", [])
        ]
        return {
            "pnu": pnu,
            # A VWorld planning designation can be a restriction such as a
            # river zone, not the statutory use district. Keep it separately.
            "zoning": explicit_zoning[0] if len(explicit_zoning) == 1 else None,
            "landUseZones": designations,
            "legalFar": legal_far,
            "legalBcr": legal_bcr,
            "districtUnitPlan": district_unit_plan,
            "coverage": "complete" if not missing_fields else "partial",
            "sourcePnuVerified": True,
            "missingFields": missing_fields,
            "landUsePlanItems": [
                {
                    "pnu": _pick(item, ["pnu"]),
                    "designationCode": _pick(item, ["prposAreaDstrcCode", "zoningCode", "zoneCode"]),
                    "designationName": _pick(item, ["prposAreaDstrcCodeNm", "zoning", "zone", "useDistrict", "용도지역명"]),
                    "conflictCode": _pick(item, ["cnflcAt"]),
                    "conflictName": _pick(item, ["cnflcAtNm"]),
                    "registeredDate": _pick(item, ["registDt"]),
                    "lastUpdatedDate": _pick(item, ["lastUpdtDt", "baseDate", "effectiveDate"]),
                }
                for item in normalized_items
            ],
            "sourceName": source_name,
            "sourceType": "public_api",
            "sourceUrlOrEvidenceId": source_url,
            "baseDate": next((_pick(item, ["lastUpdtDt", "baseDate", "effectiveDate", "stdrDe", "기준일"]) for item in normalized_items if _pick(item, ["lastUpdtDt", "baseDate", "effectiveDate", "stdrDe", "기준일"]) is not None), None),
            "fetchedAt": _now(),
            "confidence": 0.95,
        }

    def lookup_parcel_address(self, pnu: str, *, timeout_seconds: float) -> Dict[str, Any] | None:
        """Read address fields from a land-characteristics response with an exact PNU."""
        result = self._fetch_land_characteristics_record(
            pnu, standard_year=None, parcel={"pnu": pnu}, timeout_seconds=timeout_seconds,
        )
        return result.get("record")

    def _fetch_land_characteristics_record(
        self,
        pnu: str,
        *,
        standard_year: int | str | None,
        parcel: Dict[str, Any],
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        """Fetch source-backed parcel characteristics from VWorld."""
        api_key = _first_env(self.environ, ["REALYTICS_VWORLD_API_KEY", "VWORLD_API_KEY"])
        if not api_key:
            return {"record": None, "diagnostics": [_diagnostic(
                connector="land_characteristics",
                status="skipped_missing_api_key",
                message="VWorld land-characteristics lookup requires a configured API key.",
                parcel=parcel,
            )]}
        params = {
            "pnu": pnu,
            "stdrYear": str(standard_year or self.environ.get("REALYTICS_LAND_CHARACTERISTICS_YEAR") or datetime.now(timezone.utc).year),
            "format": "json",
            "numOfRows": "1000",
            "pageNo": "1",
            "key": api_key,
        }
        domain = self.environ.get("VWORLD_DOMAIN") or self.environ.get("REALYTICS_VWORLD_DOMAIN")
        if domain:
            params["domain"] = domain
        url = f"{VWORLD_LAND_CHARACTERISTICS_URL}?{urllib.parse.urlencode(params)}"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "REALYTICS/land-data-connectors-v1"})
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            return {"record": None, "diagnostics": [_diagnostic(
                connector="land_characteristics",
                status="fetch_failed",
                message=f"{exc.__class__.__name__}: {exc}",
                parcel=parcel,
                url=_redact_service_key(url),
            )]}

        items = _payload_items(_parse_payload(body))
        item = next((row for row in items if _response_matches_pnu(row, pnu)), None)
        record = _normalize_land_characteristics_record(item, pnu, _redact_service_key(url)) if item else None
        return {
            "record": record,
            "diagnostics": [_diagnostic(
                connector="land_characteristics",
                status="fetched" if record else "pnu_unverified" if items else "no_record",
                message=(
                    "VWorld land-characteristics attributes returned source-backed parcel characteristics."
                    if record else "VWorld land-characteristics attributes returned no usable record for the PNU."
                ),
                parcel=parcel,
                url=_redact_service_key(url),
            )],
        }

    def _fetch_region_land_price_change(
        self,
        region_code: str,
        *,
        standard_year: int | str | None,
        standard_month: int | str | None,
        scope_div: str | None,
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        """Fetch monthly/cumulative regional land-price change evidence once per run."""
        api_key = _first_env(self.environ, ["REALYTICS_VWORLD_API_KEY", "VWORLD_API_KEY"])
        if not api_key:
            return {"records": [], "diagnostics": [_diagnostic(
                connector="region_land_price_trend",
                status="skipped_missing_api_key",
                message="VWorld regional land-price lookup requires VWORLD_API_KEY or REALYTICS_VWORLD_API_KEY.",
                parcel={"pnu": region_code},
            )]}

        year = standard_year or self.environ.get("REALYTICS_LAND_PRICE_TREND_YEAR")
        if not year:
            year = datetime.now(timezone.utc).year
        month = standard_month or self.environ.get("REALYTICS_LAND_PRICE_TREND_MONTH")
        params = {
            "stdrYear": str(year),
            "reqLdCode": str(region_code)[:10],
            "scopeDiv": (scope_div or self.environ.get("REALYTICS_LAND_PRICE_TREND_SCOPE") or "A").upper(),
            "format": "json",
            "numOfRows": "1000",
            "pageNo": "1",
            "key": api_key,
        }
        if month:
            params["stdrMt"] = str(month).zfill(2)
        if params["scopeDiv"] == "A":
            params["reqLdCode"] = str(region_code)[:5]
        domain = self.environ.get("VWORLD_DOMAIN") or self.environ.get("REALYTICS_VWORLD_DOMAIN")
        if domain:
            params["domain"] = domain
        url = f"{VWORLD_REGION_LAND_PRICE_CHANGE_URL}?{urllib.parse.urlencode(params)}"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "REALYTICS/land-data-connectors-v1"})
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            return {"records": [], "diagnostics": [_diagnostic(
                connector="region_land_price_trend",
                status="fetch_failed",
                message=f"{exc.__class__.__name__}: {exc}",
                parcel={"pnu": region_code},
                url=_redact_service_key(url),
            )]}

        items = _payload_items(_parse_payload(body))
        records = [
            record for item in items
            if (record := _normalize_region_land_price_change(item, region_code, _redact_service_key(url)))
        ]
        return {
            "records": records,
            "diagnostics": [_diagnostic(
                connector="region_land_price_trend",
                status="fetched" if records else "no_record",
                message=(
                    "VWorld regional land-price change attributes returned source-backed monthly and cumulative rates."
                    if records else "VWorld regional land-price change attributes returned no usable record."
                ),
                parcel={"pnu": region_code},
                url=_redact_service_key(url),
            )],
        }

    def _fetch_building_use_records(
        self,
        pnu: str,
        *,
        parcel: Dict[str, Any],
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        """Fetch source-backed building-use rows for a parcel from VWorld."""
        api_key = _first_env(self.environ, ["REALYTICS_VWORLD_API_KEY", "VWORLD_API_KEY"])
        if not api_key:
            return {"records": [], "diagnostics": [_diagnostic(
                connector="building_use",
                status="skipped_missing_api_key",
                message="VWorld building-use lookup requires VWORLD_API_KEY or REALYTICS_VWORLD_API_KEY.",
                parcel=parcel,
            )]}

        params = {
            "pnu": pnu,
            "format": "json",
            "numOfRows": "1000",
            "pageNo": "1",
            "key": api_key,
        }
        domain = self.environ.get("VWORLD_DOMAIN") or self.environ.get("REALYTICS_VWORLD_DOMAIN")
        if domain:
            params["domain"] = domain
        url = f"{VWORLD_BUILDING_USE_URL}?{urllib.parse.urlencode(params)}"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "REALYTICS/land-data-connectors-v1"})
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            return {"records": [], "diagnostics": [_diagnostic(
                connector="building_use",
                status="fetch_failed",
                message=f"{exc.__class__.__name__}: {exc}",
                parcel=parcel,
                url=_redact_service_key(url),
            )]}

        items = _payload_items(_parse_payload(body))
        records: List[Dict[str, Any]] = []
        for item in items:
            if not _response_matches_pnu(item, pnu):
                continue
            record = _normalize_building_use_record(item, pnu, _redact_service_key(url))
            if record:
                records.append(record)
        status = "fetched" if records else "no_record"
        message = (
            "VWorld building-use attributes returned source-backed building records."
            if records
            else "VWorld building-use attributes returned no usable building record for the PNU."
        )
        return {
            "records": records,
            "diagnostics": [_diagnostic(
                connector="building_use",
                status=status,
                message=message,
                parcel=parcel,
                url=_redact_service_key(url),
            )],
        }

    def _fetch_official_land_price(
        self,
        pnu: str,
        *,
        base_year: int | str | None,
        parcel: Dict[str, Any],
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        template = self.environ.get("REALYTICS_OFFICIAL_LAND_PRICE_URL_TEMPLATE")
        api_key = _first_env(self.environ, ["REALYTICS_PUBLIC_DATA_API_KEY", "DATA_GO_KR_API_KEY", "REALYTICS_VWORLD_API_KEY", "VWORLD_API_KEY"])
        if not template:
            if api_key:
                properties, _diagnostic_result = fetch_parcel_by_pnu(
                    pnu,
                    environ=self.environ,
                    timeout=timeout_seconds,
                )
                price = _to_float(_pick(properties or {}, ["jiga", "pricePerSqm", "officialLandPricePerSqm", "pblntfPclnd"]))
                if price is not None and _response_matches_pnu(properties or {}, pnu):
                    source_id = f"vworld:{pnu}:LP_PA_CBND_BUBUN:jiga"
                    return {
                        "record": {
                            "pnu": pnu,
                            "pricePerSqm": price,
                            "sourcePnuVerified": True,
                            "baseYear": _pick(properties or {}, ["baseYear", "stdrYear"]) or base_year,
                            "baseDate": _pick(properties or {}, ["baseDate", "stdrDe", "pblntfDe"]),
                            "sourceName": "VWorld cadastral jiga",
                            "sourceUrlOrEvidenceId": source_id,
                            "fetchedAt": _now(),
                            "confidence": 0.95,
                        },
                        "diagnostics": [_diagnostic(
                            connector="official_land_price",
                            status="fetched",
                            message="VWorld cadastral response returned a source-backed official land price field.",
                            parcel=parcel,
                            url=source_id,
                        )],
                    }
            return {"record": None, "diagnostics": [_diagnostic(
                connector="official_land_price",
                status=("fetch_failed" if _diagnostic_result.get("status") == "error"
                        else "pnu_unverified" if properties and not _response_matches_pnu(properties, pnu)
                        else "no_record") if api_key else "skipped_missing_config",
                message="Official land price lookup returned no usable PNU-matched price or has no configured connector.",
                parcel=parcel,
            )]}
        if "{service_key}" in template and not api_key:
            return {"record": None, "diagnostics": [_diagnostic(
                connector="official_land_price",
                status="skipped_missing_api_key",
                message="Official land price connector requires REALYTICS_PUBLIC_DATA_API_KEY, DATA_GO_KR_API_KEY, REALYTICS_VWORLD_API_KEY, or VWORLD_API_KEY.",
                parcel=parcel,
            )]}

        response = self._fetch_template(
            template,
            pnu=pnu,
            service_key=api_key,
            base_year=base_year,
            timeout_seconds=timeout_seconds,
        )
        if response.get("error"):
            return {"record": None, "diagnostics": [_diagnostic(
                connector="official_land_price",
                status="fetch_failed",
                message=response["error"],
                parcel=parcel,
                url=response.get("url"),
            )]}

        data = _parse_payload(response.get("body", ""))
        items = _payload_items(data)
        item = next((row for row in items if _response_matches_pnu(row, pnu)), None)
        if not item:
            return {"record": None, "diagnostics": [_diagnostic(
                connector="official_land_price",
                status="pnu_unverified" if items else "no_record",
                message="Official land price connector returned no row with the requested PNU.",
                parcel=parcel,
                url=response.get("url"),
            )]}

        price = _to_float(_pick(item, [
            "pricePerSqm",
            "officialLandPricePerSqm",
            "pblntfPclnd",
            "공시지가",
            "price",
        ]))
        if price is None:
            return {"record": None, "diagnostics": [_diagnostic(
                connector="official_land_price",
                status="record_missing_price",
                message="Official land price connector returned a record without a recognizable price field.",
                parcel=parcel,
                url=response.get("url"),
            )]}

        return {
            "record": {
                "pnu": pnu,
                "pricePerSqm": price,
                "sourcePnuVerified": True,
                "baseYear": _pick(item, ["baseYear", "stdrYear", "기준년도"]) or base_year,
                "baseDate": _pick(item, ["baseDate", "stdrDe", "pblntfDe", "기준일"]),
                "sourceName": self.environ.get("REALYTICS_OFFICIAL_LAND_PRICE_SOURCE_NAME", "configured_official_land_price_connector"),
                "sourceUrlOrEvidenceId": response.get("url"),
                "fetchedAt": _now(),
                "confidence": 0.95,
            },
            "diagnostics": [_diagnostic(
                connector="official_land_price",
                status="fetched",
                message="Official land price connector returned a source-backed record.",
                parcel=parcel,
                url=response.get("url"),
            )],
        }

    def _fetch_template(
        self,
        template: str,
        *,
        pnu: str,
        service_key: str | None,
        base_year: int | str | None,
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        url = template.format(
            pnu=urllib.parse.quote(str(pnu), safe=""),
            service_key=urllib.parse.quote(str(service_key or ""), safe=""),
            base_year=urllib.parse.quote(str(base_year or ""), safe=""),
        )
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "REALYTICS/land-data-connectors-v1"})
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
            return {"url": _redact_service_key(url), "body": body}
        except Exception as exc:
            return {"url": _redact_service_key(url), "error": f"{exc.__class__.__name__}: {exc}"}


def generate_pnu_from_parcel(parcel: Dict[str, Any], legal_dong_code: str) -> Optional[Dict[str, Any]]:
    if not (legal_dong_code and len(str(legal_dong_code)) == 10 and str(legal_dong_code).isdigit()):
        return None
    main = parcel.get("mainLotNo") or parcel.get("main_lot_no")
    sub = parcel.get("subLotNo") if parcel.get("subLotNo") is not None else parcel.get("sub_lot_no", 0)
    if main is None:
        return None
    mountain_flag = "2" if parcel.get("isMountain") or parcel.get("is_mountain") else "1"
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


def _diagnostic(
    *,
    connector: str,
    status: str,
    message: str,
    parcel: Dict[str, Any],
    url: str | None = None,
) -> Dict[str, Any]:
    diagnostic = {
        "connector": connector,
        "status": status,
        "message": message,
        "sequence": parcel.get("sequence"),
        "addressFragment": parcel.get("addressFragment"),
        "pnu": parcel.get("pnu") or parcel.get("resolvedPnu"),
    }
    if url:
        diagnostic["url"] = url
    return diagnostic


def _first_env(environ: Dict[str, str], keys: List[str]) -> str | None:
    for key in keys:
        value = environ.get(key)
        if value:
            return value
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_payload(body: str) -> Any:
    stripped = (body or "").strip()
    if not stripped:
        return None
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return None
    try:
        root = ET.fromstring(stripped)
    except ET.ParseError:
        return None
    return _xml_to_dict(root)


def _xml_to_dict(element: ET.Element) -> Any:
    children = list(element)
    if not children:
        return element.text
    grouped: Dict[str, Any] = {}
    for child in children:
        value = _xml_to_dict(child)
        if child.tag in grouped:
            if not isinstance(grouped[child.tag], list):
                grouped[child.tag] = [grouped[child.tag]]
            grouped[child.tag].append(value)
        else:
            grouped[child.tag] = value
    return grouped


def _response_matches_pnu(item: Dict[str, Any], requested_pnu: str) -> bool:
    """Accept cadastral facts only when the source row identifies the same parcel."""
    returned_pnus = {
        str(item[key]).strip()
        for key in ("pnu", "PNU")
        if item.get(key) not in (None, "")
    }
    return returned_pnus == {str(requested_pnu).strip()}


def _payload_items(data: Any) -> List[Dict[str, Any]]:
    """Return source rows from common JSON/XML envelopes without inventing rows."""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("properties"), dict):
        return [data["properties"]]
    for key in ["item", "items", "field", "fields", "row", "rows", "data", "result", "response", "body"]:
        value = data.get(key)
        if isinstance(value, (dict, list)):
            nested = _payload_items(value)
            if nested:
                return nested
    if any(not isinstance(value, (dict, list)) for value in data.values()):
        return [data]
    for value in data.values():
        nested = _payload_items(value)
        if nested:
            return nested
    return []


def _unique_strings(values: Any) -> List[str]:
    unique: List[str] = []
    for value in values:
        if value in (None, ""):
            continue
        text = str(value).strip()
        if text and text not in unique:
            unique.append(text)
    return unique


def _normalize_land_characteristics_record(item: Dict[str, Any] | None, pnu: str, source_url: str) -> Dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    if not any(_pick(item, [key]) is not None for key in ["lndpclAr", "lndcgrCodeNm", "prposArea1Nm", "pblntfPclnd"]):
        return None
    return {
        "pnu": _pick(item, ["pnu"]) or pnu,
        "sourcePnuVerified": True,
        "legalDongCode": _pick(item, ["ldCode"]),
        "legalDongName": _pick(item, ["ldCodeNm"]),
        "ledgerTypeCode": _pick(item, ["regstrSeCode"]),
        "ledgerTypeName": _pick(item, ["regstrSeCodeNm"]),
        "lotNumber": _pick(item, ["mnnmSlno"]),
        "landSerialNumber": _pick(item, ["ladSn"]),
        "standardYear": _pick(item, ["stdrYear"]),
        "standardMonth": _pick(item, ["stdrMt"]),
        "landCategoryCode": _pick(item, ["lndcgrCode"]),
        "landCategoryName": _pick(item, ["lndcgrCodeNm"]),
        "areaSqm": _to_float(_pick(item, ["lndpclAr"])),
        "useDistrictCode1": _pick(item, ["prposArea1"]),
        "useDistrictName1": _pick(item, ["prposArea1Nm"]),
        "useDistrictCode2": _pick(item, ["prposArea2"]),
        "useDistrictName2": _pick(item, ["prposArea2Nm"]),
        "landUseSituationCode": _pick(item, ["ladUseSittn"]),
        "landUseSituationName": _pick(item, ["ladUseSittnNm"]),
        "terrainHeightCode": _pick(item, ["tpgrphHgCode"]),
        "terrainHeightName": _pick(item, ["tpgrphHgCodeNm"]),
        "terrainShapeCode": _pick(item, ["tpgrphFrmCode"]),
        "terrainShapeName": _pick(item, ["tpgrphFrmCodeNm"]),
        "roadSideCode": _pick(item, ["roadSideCode"]),
        "roadSideName": _pick(item, ["roadSideCodeNm"]),
        "officialLandPricePerSqm": _to_float(_pick(item, ["pblntfPclnd"])),
        "baseDate": _pick(item, ["lastUpdtDt"]),
        "sourceName": "VWorld land characteristics",
        "sourceType": "public_api",
        "sourceUrlOrEvidenceId": source_url,
        "fetchedAt": _now(),
        "confidence": 0.95,
    }


def _normalize_building_use_record(item: Dict[str, Any], pnu: str, source_url: str) -> Dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    if not any(_pick(item, [key]) is not None for key in [
        "gisIdntfcNo", "buldIdntfcNo", "buldMainAtachSeCodeNm", "mainPrposCodeNm",
        "buldTotar", "buldBildngAr", "buldPlotAr",
    ]):
        return None
    numeric_keys = {
        "siteAreaSqm": ["buldPlotAr"],
        "buildingAreaSqm": ["buldBildngAr"],
        "totalFloorAreaSqm": ["buldTotar"],
        "floorAreaRatio": ["measrmtRt"],
        "buildingCoverageRatio": ["btlRt"],
        "heightM": ["buldHg"],
        "groundFloors": ["groundFloorCo"],
        "undergroundFloors": ["undgrndFloorCo"],
    }
    record: Dict[str, Any] = {
        "pnu": _pick(item, ["pnu"]) or pnu,
        "gisBuildingId": _pick(item, ["gisIdntfcNo"]),
        "buildingId": _pick(item, ["buldIdntfcNo"]),
        "legalDongCode": _pick(item, ["ldCode"]),
        "legalDongName": _pick(item, ["ldCodeNm"]),
        "lotNumber": _pick(item, ["mnnmSlno"]),
        "buildingName": _pick(item, ["buldNm"]),
        "buildingDongName": _pick(item, ["buldDongNm"]),
        "buildingMainAttached": _pick(item, ["buldMainAtachSeCodeNm"]),
        "buildingLedgerType": _pick(item, ["buldKndCodeNm"]),
        "buildingStructure": _pick(item, ["strctCodeNm"]),
        "mainUseCode": _pick(item, ["mainPrposCode"]),
        "mainUseName": _pick(item, ["mainPrposCodeNm"]),
        "detailUseCode": _pick(item, ["detailPrposCode"]),
        "detailUseName": _pick(item, ["detailPrposCodeNm"]),
        "buildingUseClassCode": _pick(item, ["buldPrposClCode"]),
        "buildingUseClassName": _pick(item, ["buldPrposClCodeNm"]),
        "permitDate": _pick(item, ["prmisnDe"]),
        "approvalDate": _pick(item, ["useConfmDe"]),
        "baseDate": _pick(item, ["lastUpdtDt"]),
        "sourceName": "VWorld building use attributes",
        "sourceType": "public_api",
        "sourceUrlOrEvidenceId": source_url,
        "fetchedAt": _now(),
        "confidence": 0.95,
    }
    for output_key, source_keys in numeric_keys.items():
        raw_value = _pick(item, source_keys)
        record[output_key] = _to_float(raw_value)
    return record


def _normalize_region_land_price_change(item: Dict[str, Any], region_code: str, source_url: str) -> Dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    if not any(_pick(item, [key]) is not None for key in ["stdrYear", "pclndIndex", "pclndChgRt", "acmtlPclndChgRt"]):
        return None
    return {
        "regionCode": region_code,
        "standardYear": _pick(item, ["stdrYear"]),
        "standardMonth": _pick(item, ["stdrMt"]),
        "provinceCode": _pick(item, ["ldCtprvnCode"]),
        "provinceName": _pick(item, ["ldCtprvnNm"]),
        "cityCountyCode": _pick(item, ["ldCpsgCode"]),
        "cityCountyName": _pick(item, ["ldCpsgCodeNm"]),
        "townVillageCode": _pick(item, ["ldEmdLiCode"]),
        "townVillageName": _pick(item, ["ldEmdLiCodeNm"]),
        "landPriceIndex": _to_float(_pick(item, ["pclndIndex"])),
        "monthlyChangeRate": _to_float(_pick(item, ["pclndChgRt"])),
        "cumulativeChangeRate": _to_float(_pick(item, ["acmtlPclndChgRt"])),
        "baseDate": _pick(item, ["lastUpdtDt"]),
        "sourceName": "VWorld regional land-price change attributes",
        "sourceType": "public_api",
        "sourceUrlOrEvidenceId": source_url,
        "fetchedAt": _now(),
        "confidence": 0.95,
    }


def _pick(item: Dict[str, Any], keys: List[str]) -> Any:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return None


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _redact_service_key(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted = [
        (key, "***" if key.lower() in {"servicekey", "service_key", "key", "api_key", "apikey"} else value)
        for key, value in query
    ]
    return urllib.parse.urlunsplit((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        urllib.parse.urlencode(redacted),
        parsed.fragment,
    ))
