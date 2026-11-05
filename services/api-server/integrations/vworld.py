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
    geometry = _matching_parcel_geometry(features, pnu)
    if geometry is None:
        return None, _diag(pnu, "not_found", "no matching parcel boundary in VWorld response")
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
    resp = payload.get("response") or {}
    point = ((resp.get("result") or {}).get("point")) or {}
    structure = (resp.get("refined") or {}).get("structure") or {}
    diag = {"address": address, "status": "ok", "structure": structure}
    try:
        return {"lng": float(point["x"]), "lat": float(point["y"])}, diag
    except (KeyError, TypeError, ValueError):
        return None, {"address": address, "status": "not_found", "message": "no point in response"}


def resolve_legal_dong_code(address: str, *, environ: Optional[Dict[str, str]] = None, timeout: float = 8.0) -> Optional[str]:
    """The 10-digit 법정동코드 for an address, via the VWorld geocoder's parsed
    structure (``refined.structure.level4LC`` is a 19-digit PNU; its first 10
    digits are the legal-dong code). Returns None when VWorld cannot parse it —
    never fabricated. Used to construct per-지번 PNUs precisely.
    """
    _, diag = geocode_address(address, environ=environ, timeout=timeout)
    level4lc = str((diag.get("structure") or {}).get("level4LC") or "").strip()
    return level4lc[:10] if len(level4lc) >= 10 and level4lc[:10].isdigit() else None


def build_pnu(legal_dong_code_10: str, lot_number: str, *, mountain: bool = False) -> Optional[str]:
    """Compose a 19-digit PNU from a 10-digit legal-dong code + a 지번.

    PNU = legalDong(10) + 산여부(1: 1 일반, 2 산) + 본번(4) + 부번(4). Returns None
    when the inputs are not well-formed (no fabrication of a malformed PNU).
    """
    if not (legal_dong_code_10 and legal_dong_code_10.isdigit() and len(legal_dong_code_10) == 10):
        return None
    raw = str(lot_number or "").strip()
    if not raw:
        return None
    if raw.startswith("산"):
        mountain = True
        raw = raw[1:].strip()
    parts = raw.split("-", 1)
    try:
        bon = int(parts[0])
        bu = int(parts[1]) if len(parts) > 1 and parts[1] else 0
    except (ValueError, TypeError):
        return None
    if bon > 9999 or bu > 9999 or bon < 0 or bu < 0:
        return None
    return f"{legal_dong_code_10}{'2' if mountain else '1'}{bon:04d}{bu:04d}"


def fetch_parcel_by_pnu(
    pnu: str,
    *,
    environ: Optional[Dict[str, str]] = None,
    timeout: float = 8.0,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Look up a parcel's attributes (pnu/jibun/addr/jiga) by an exact PNU via the
    VWorld cadastral data layer (attrFilter, no geometry). Returns (properties,
    diag); status 'resolved' only when VWorld confirms the PNU exists —
    'not_found' means the 지번 is not in the current cadastre (e.g. merged/
    renumbered), never a fabricated match."""
    env = environ if environ is not None else os.environ
    key = _api_key(env)
    if not key:
        return None, {"status": "skipped", "message": "VWorld key not configured"}
    if not pnu:
        return None, {"status": "skipped", "message": "missing PNU"}
    params = {
        "service": "data",
        "request": "GetFeature",
        "data": CADASTRAL_LAYER,
        "key": key,
        "domain": env.get("VWORLD_DOMAIN") or "localhost",
        "format": "json",
        "geometry": "false",
        "size": "1",
        "attrFilter": f"pnu:=:{pnu}",
    }
    url = f"{VWORLD_DATA_URL}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return None, {"status": "error", "message": f"VWorld request failed: {type(exc).__name__}"}
    resp = (payload or {}).get("response") or {}
    if resp.get("status") != "OK":
        return None, {"status": "not_found" if resp.get("status") == "NOT_FOUND" else "error", "pnu": pnu}
    features = ((resp.get("result") or {}).get("featureCollection") or {}).get("features") or []
    if not features:
        return None, {"status": "not_found", "pnu": pnu}
    for feature in features:
        properties = feature.get("properties") or {}
        if str(properties.get("pnu") or "").strip() == pnu:
            return properties, {"status": "resolved", "pnu": pnu}
    return None, {"status": "pnu_mismatch", "pnu": pnu}


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


def fetch_jiga_in_radius(
    lng: float,
    lat: float,
    buffer_m: int,
    *,
    environ: Optional[Dict[str, str]] = None,
    size: int = 1000,
    timeout: float = 15.0,
) -> Tuple[List[float], int, Dict[str, Any]]:
    """개별공시지가(jiga, 원/㎡) of cadastral parcels within `buffer_m` of a point.

    Returns (jiga_values, total_in_radius, diagnostic). `total` may exceed
    len(values) when capped at `size` (dense areas) — the values are then a
    representative sample. Non-fabricating: errors → ([], 0, diag)."""
    env = environ if environ is not None else os.environ
    key = _api_key(env)
    if not key:
        return [], 0, {"status": "skipped", "message": "VWorld key not configured"}
    params = {
        "service": "data",
        "request": "GetFeature",
        "data": CADASTRAL_LAYER,
        "key": key,
        "domain": env.get("VWORLD_DOMAIN") or "localhost",
        "format": "json",
        "geometry": "false",
        "size": str(size),
        "geomFilter": f"POINT({lng} {lat})",
        "buffer": str(int(buffer_m)),
    }
    url = f"{VWORLD_DATA_URL}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return [], 0, {"status": "error", "message": f"VWorld request failed: {type(exc).__name__}"}
    resp = (payload or {}).get("response") or {}
    if resp.get("status") != "OK":
        return [], 0, {"status": resp.get("status", "error")}
    total = 0
    try:
        total = int((resp.get("record") or {}).get("total") or 0)
    except (TypeError, ValueError):
        total = 0
    features = ((resp.get("result") or {}).get("featureCollection") or {}).get("features") or []
    values: List[float] = []
    for feature in features:
        raw = (feature.get("properties") or {}).get("jiga") if isinstance(feature, dict) else None
        try:
            v = float(str(raw).replace(",", "")) if raw not in (None, "", "0") else None
        except (TypeError, ValueError):
            v = None
        if v:
            values.append(v)
    return values, total, {"status": "ok", "returned": len(features)}


def build_geocoder(environ: Optional[Dict[str, str]] = None):
    """Return geocode(address)->{lng,lat}|None, collecting diagnostics."""
    diagnostics: List[Dict[str, Any]] = []

    def geocode(address: str) -> Optional[Dict[str, float]]:
        point, diag = geocode_address(address, environ=environ)
        diagnostics.append(diag)
        return point

    geocode.diagnostics = diagnostics  # type: ignore[attr-defined]
    return geocode


def _matching_parcel_geometry(features: Any, pnu: str) -> Optional[Dict[str, Any]]:
    if not isinstance(features, list):
        return None
    for feature in features:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties")
        if not isinstance(properties, dict) or str(properties.get("pnu") or "").strip() != pnu:
            continue
        geometry = feature.get("geometry")
        if (
            isinstance(geometry, dict)
            and geometry.get("type") in ("Polygon", "MultiPolygon")
            and geometry.get("coordinates")
        ):
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
