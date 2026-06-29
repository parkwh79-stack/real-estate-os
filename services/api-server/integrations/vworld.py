from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

VWORLD_DATA_URL = "https://api.vworld.kr/req/data"
VWORLD_ADDRESS_URL = "https://api.vworld.kr/req/address"
# Continuous cadastral map (연속지적도) — parcel boundary polygons keyed by PNU.
CADASTRAL_LAYER = "LP_PA_CBND_BUBUN"

_KEY_ENV = ["VWORLD_API_KEY", "REALYTICS_VWORLD_API_KEY", "REALYTICS_PUBLIC_DATA_API_KEY", "DATA_GO_KR_API_KEY"]


def _api_key(environ: Optional[Dict[str, str]] = None) -> Optional[str]:
    env = environ if environ is not None else os.environ
    for key in _KEY_ENV:
        value = env.get(key)
        if value and value.strip():
            return value.strip()
    return None


def vworld_configured(environ: Optional[Dict[str, str]] = None) -> bool:
    return _api_key(environ) is not None


def vworld_status(environ: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Secret-safe readiness for the map/spatial layer (never echoes the key)."""
    env = environ if environ is not None else os.environ
    return {
        "configured": vworld_configured(env),
        "dataUrl": VWORLD_DATA_URL,
        "cadastralLayer": CADASTRAL_LAYER,
        "domain": (env.get("VWORLD_DOMAIN") or "localhost"),
    }


def fetch_parcel_geometry_by_pnu(
    pnu: str,
    *,
    environ: Optional[Dict[str, str]] = None,
    timeout: float = 8.0,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Fetch the cadastral boundary geometry (GeoJSON) for a PNU from VWorld.

    Returns (geometry, diagnostic). geometry is a GeoJSON geometry dict or None.
    Never raises and never fabricates — any missing key, network error, or empty
    result yields (None, diagnostic) so the caller degrades gracefully.
    """
    env = environ if environ is not None else os.environ
    key = _api_key(env)
    if not key:
        return None, _diag(pnu, "skipped", "VWorld API key not configured")
    if not pnu:
        return None, _diag(pnu, "skipped", "missing PNU")

    params = {
        "service": "data",
        "request": "GetFeature",
        "data": CADASTRAL_LAYER,
        "key": key,
        "domain": env.get("VWORLD_DOMAIN") or "localhost",
        "format": "json",
        "geometry": "true",
        "crs": "EPSG:4326",
        "size": "1",
        "attrFilter": f"pnu:=:{pnu}",
    }
    url = f"{VWORLD_DATA_URL}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 (trusted host)
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # network / decode / timeout — degrade, never crash
        return None, _diag(pnu, "error", f"VWorld request failed: {type(exc).__name__}")

    status = (((payload or {}).get("response") or {}).get("status")) or "UNKNOWN"
    if status != "OK":
        return None, _diag(pnu, "not_found" if status == "NOT_FOUND" else "error", f"VWorld status {status}")
    features = (
        (((payload.get("response") or {}).get("result") or {}).get("featureCollection") or {}).get("features")
    )
    geometry = _first_geometry(features)
    if geometry is None:
        return None, _diag(pnu, "not_found", "no geometry in VWorld feature")
    return geometry, _diag(pnu, "resolved", "VWorld cadastral boundary")


def geocode_address(
    address: str,
    *,
    environ: Optional[Dict[str, str]] = None,
    addr_type: str = "PARCEL",
    timeout: float = 8.0,
) -> Tuple[Optional[Dict[str, float]], Dict[str, Any]]:
    """Geocode a Korean address to {lng, lat} via the VWorld address API.

    Best-effort & non-fabricating: missing key / network error / not-found →
    (None, diagnostic). addr_type "PARCEL" for 지번, "ROAD" for 도로명.
    """
    env = environ if environ is not None else os.environ
    key = _api_key(env)
    if not key:
        return None, {"address": address, "status": "skipped", "message": "VWorld key not configured"}
    if not address or not address.strip():
        return None, {"address": address, "status": "skipped", "message": "empty address"}
    params = {
        "service": "address",
        "request": "getcoord",
        "version": "2.0",
        "crs": "epsg:4326",
        "address": address.strip(),
        "format": "json",
        "type": addr_type,
        "key": key,
    }
    url = f"{VWORLD_ADDRESS_URL}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return None, {"address": address, "status": "error", "message": f"geocode failed: {type(exc).__name__}"}
    status = (((payload or {}).get("response") or {}).get("status")) or "UNKNOWN"
    if status != "OK":
        return None, {"address": address, "status": "not_found", "message": f"VWorld status {status}"}
    point = (((payload.get("response") or {}).get("result") or {}).get("point")) or {}
    try:
        return {"lng": float(point["x"]), "lat": float(point["y"])}, {"address": address, "status": "ok"}
    except (KeyError, TypeError, ValueError):
        return None, {"address": address, "status": "not_found", "message": "no point in response"}


def fetch_parcel_by_point(
    lng: float,
    lat: float,
    *,
    environ: Optional[Dict[str, str]] = None,
    timeout: float = 8.0,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Fetch the cadastral parcel at a coordinate — returns (geometry, properties).

    Properties carry the parcel's pnu/jibun/addr from the VWorld 연속지적도.
    Best-effort & non-fabricating."""
    env = environ if environ is not None else os.environ
    key = _api_key(env)
    if not key:
        return None, {"status": "skipped", "message": "VWorld key not configured"}
    params = {
        "service": "data",
        "request": "GetFeature",
        "data": CADASTRAL_LAYER,
        "key": key,
        "domain": env.get("VWORLD_DOMAIN") or "localhost",
        "format": "json",
        "geometry": "true",
        "crs": "EPSG:4326",
        "size": "1",
        "geomFilter": f"POINT({lng} {lat})",
    }
    url = f"{VWORLD_DATA_URL}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return None, {"status": "error", "message": f"VWorld request failed: {type(exc).__name__}"}
    status = (((payload or {}).get("response") or {}).get("status")) or "UNKNOWN"
    if status != "OK":
        return None, {"status": "not_found", "message": f"VWorld status {status}"}
    features = (
        (((payload.get("response") or {}).get("result") or {}).get("featureCollection") or {}).get("features")
    )
    if not isinstance(features, list) or not features:
        return None, {"status": "not_found", "message": "no parcel at point"}
    feature = features[0]
    geometry = feature.get("geometry") if isinstance(feature, dict) else None
    properties = feature.get("properties") if isinstance(feature, dict) else None
    if not (isinstance(geometry, dict) and geometry.get("type")):
        return None, {"status": "not_found", "message": "no geometry"}
    return geometry, {"status": "resolved", "properties": properties or {}}


def build_geocoder(environ: Optional[Dict[str, str]] = None):
    """Return geocode(address)->{lng,lat}|None, collecting diagnostics."""
    diagnostics: List[Dict[str, Any]] = []

    def geocode(address: str) -> Optional[Dict[str, float]]:
        point, diag = geocode_address(address, environ=environ)
        diagnostics.append(diag)
        return point

    geocode.diagnostics = diagnostics  # type: ignore[attr-defined]
    return geocode


def _first_geometry(features: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(features, list):
        return None
    for feature in features:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        if isinstance(geometry, dict) and geometry.get("type") and geometry.get("coordinates"):
            return geometry
    return None


def _diag(pnu: str, status: str, message: str) -> Dict[str, Any]:
    return {"pnu": pnu, "connector": "vworld_cadastral", "status": status, "message": message}


def build_pnu_geometry_resolver(environ: Optional[Dict[str, str]] = None):
    """Return resolver(pnu) -> geometry|None for map geometry enrichment."""
    diagnostics: List[Dict[str, Any]] = []

    def resolver(pnu: str) -> Optional[Dict[str, Any]]:
        geometry, diag = fetch_parcel_geometry_by_pnu(pnu, environ=environ)
        diagnostics.append(diag)
        return geometry

    resolver.diagnostics = diagnostics  # type: ignore[attr-defined]
    return resolver
