from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


CONNECTOR_ROUTER_VERSION = "realytics-land-data-connectors-v1"


class LandDataConnectorRouter:
    """Fetch parcel enrichment records from configured external connectors.

    The router is intentionally conservative. It only calls a connector when the
    required URL template and API key are configured, and every returned record
    carries source metadata. Missing connector configuration is reported as a
    diagnostic instead of silently manufacturing official data.
    """

    def __init__(self, environ: Optional[Dict[str, str]] = None) -> None:
        self.environ = environ or os.environ

    def fetch_records_for_parcels(
        self,
        parcels: List[Dict[str, Any]],
        *,
        legal_dong_code: str | None = None,
        base_year: int | str | None = None,
        timeout_seconds: float = 8.0,
    ) -> Dict[str, Any]:
        diagnostics: List[Dict[str, Any]] = []
        pnu_resolutions: List[Dict[str, Any]] = []
        cadastral_records: List[Dict[str, Any]] = []
        official_price_records: List[Dict[str, Any]] = []

        for parcel in parcels:
            pnu = parcel.get("pnu") or parcel.get("resolvedPnu")
            if not pnu and legal_dong_code:
                generated = generate_pnu_from_parcel(parcel, legal_dong_code)
                if generated:
                    pnu = generated["pnu"]
                    pnu_resolutions.append(generated)

            if not pnu:
                diagnostics.append(_diagnostic(
                    connector="pnu",
                    status="skipped_missing_pnu",
                    message="PNU could not be generated because legal dong code or lot fields are missing.",
                    parcel=parcel,
                ))
                continue

            cadastral_record = self._fetch_cadastral_record(
                pnu,
                parcel=parcel,
                timeout_seconds=timeout_seconds,
            )
            if cadastral_record.get("record"):
                cadastral_records.append(cadastral_record["record"])
            diagnostics.extend(cadastral_record["diagnostics"])

            official_price_record = self._fetch_official_land_price(
                pnu,
                base_year=base_year,
                parcel=parcel,
                timeout_seconds=timeout_seconds,
            )
            if official_price_record.get("record"):
                official_price_records.append(official_price_record["record"])
            diagnostics.extend(official_price_record["diagnostics"])

        return {
            "schemaVersion": CONNECTOR_ROUTER_VERSION,
            "generatedAt": _now(),
            "mode": "live_connector_router",
            "pnuResolutions": pnu_resolutions,
            "cadastralCharacteristics": cadastral_records,
            "officialLandPrices": official_price_records,
            "registryEvidence": [],
            "diagnostics": diagnostics,
            "dataSourcePolicy": {
                "pnu": "Generated only from confirmed legal dong code and normalized lot fields, or supplied by connector records.",
                "cadastral": "Fetched only when REALYTICS_CADASTRAL_URL_TEMPLATE and API key are configured.",
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
        item = _first_payload_item(data)
        if not item:
            return {"record": None, "diagnostics": [_diagnostic(
                connector="cadastral",
                status="no_record",
                message="Cadastral connector returned no usable record.",
                parcel=parcel,
                url=response.get("url"),
            )]}

        return {
            "record": {
                "pnu": pnu,
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
            return {"record": None, "diagnostics": [_diagnostic(
                connector="official_land_price",
                status="skipped_missing_config",
                message="REALYTICS_OFFICIAL_LAND_PRICE_URL_TEMPLATE is not configured.",
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
        item = _first_payload_item(data)
        if not item:
            return {"record": None, "diagnostics": [_diagnostic(
                connector="official_land_price",
                status="no_record",
                message="Official land price connector returned no usable record.",
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


def _first_payload_item(data: Any) -> Dict[str, Any] | None:
    if isinstance(data, list):
        return next((item for item in data if isinstance(item, dict)), None)
    if not isinstance(data, dict):
        return None
    for key in ["item", "items", "field", "fields", "row", "rows", "data", "result", "response"]:
        value = data.get(key)
        if isinstance(value, dict):
            nested = _first_payload_item(value)
            if nested:
                return nested
        if isinstance(value, list):
            nested = _first_payload_item(value)
            if nested:
                return nested
    if any(not isinstance(value, (dict, list)) for value in data.values()):
        return data
    for value in data.values():
        nested = _first_payload_item(value)
        if nested:
            return nested
    return None


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
