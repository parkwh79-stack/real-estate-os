from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

MAP_INTELLIGENCE_SCHEMA_VERSION = "realytics-map-intelligence-v1"

# The deck's 6-layer spatial OS. Each layer reports whether it has live data on
# this project (deterministic — derived from what is actually present), never a
# fabricated polygon. Geometry-bearing layers stay "needs_geometry" until a
# VWorld cadastral fetch / PostGIS land_parcels row supplies the shape.
_LAYER_DEFS = [
    ("parcel_boundary", "필지 경계", "VWorld 지적도 / PostGIS land_parcels"),
    ("official_land_price", "공시지가", "공공데이터포털 · 토지대장"),
    ("zoning", "용도지역", "VWorld 토지이용계획"),
    ("transactions", "실거래 사례", "실거래가 / Valuemap"),
    ("supply", "주변 공급", "분양/공급 데이터"),
    ("risk", "리스크", "Risk Register"),
]


def build_project_map_intelligence(project_data: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble a project's spatial intelligence: a parcel GeoJSON
    FeatureCollection plus the deck's 6-layer availability map.

    Deterministic and non-fabricating: a parcel with no resolved geometry
    becomes a GeoJSON Feature with ``geometry: null`` (a valid "unlocated"
    feature) and is flagged in dataLimitations — the boundary appears only once
    VWorld/PostGIS supplies the shape. No coordinates are invented.
    """
    parcels = _extract_parcels(project_data)
    features: List[Dict[str, Any]] = []
    located = 0
    priced = 0
    for index, parcel in enumerate(parcels):
        geometry = _parcel_geometry(parcel)
        properties = _parcel_properties(parcel)
        if geometry is not None:
            located += 1
        if properties.get("officialLandPricePerSqm") is not None:
            priced += 1
        features.append({
            "type": "Feature",
            "id": properties.get("pnu") or properties.get("addressFragment") or f"parcel-{index}",
            "geometry": geometry,
            "properties": properties,
        })

    feature_collection = {"type": "FeatureCollection", "features": features}
    center = _center_of(features)
    regulation = project_data.get("regulation") or {}
    risk_register = (project_data.get("riskRegister") or {})
    risk_rows = risk_register.get("risks") if isinstance(risk_register, dict) else None

    layers = _layers(
        located=located,
        priced=priced,
        parcel_count=len(parcels),
        regulation=regulation,
        risk_count=len(risk_rows) if isinstance(risk_rows, list) else 0,
    )

    return {
        "schemaVersion": MAP_INTELLIGENCE_SCHEMA_VERSION,
        "analysisType": "map_intelligence",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "center": center,
        "parcelGeoJson": feature_collection,
        "summary": {
            "parcelCount": len(parcels),
            "locatedParcelCount": located,
            "pricedParcelCount": priced,
            "hasGeometry": located > 0,
        },
        "zoning": {
            "zone": regulation.get("zone") or regulation.get("useDistrict"),
            "appliedFar": regulation.get("applied_far") or regulation.get("appliedFar"),
            "appliedBcr": regulation.get("applied_bcr") or regulation.get("appliedBcr"),
        },
        "layers": layers,
        "dataLimitations": _data_limitations(len(parcels), located, priced),
    }


def resolve_and_attach_geometry(
    project_data: Dict[str, Any],
    resolver,
    *,
    limit: int = 50,
) -> int:
    """Attach resolved boundary geometry to parcels missing it, in place.

    ``resolver(pnu) -> geometry|None`` is injected (e.g. a VWorld client), so
    this stays testable with a fake. Only fills parcels that have a PNU and no
    geometry yet; capped at ``limit``. Returns the count newly resolved. The
    pure builder then surfaces the boundaries naturally.
    """
    parcels = _extract_parcels(project_data)
    resolved = 0
    attempted = 0
    for parcel in parcels:
        if attempted >= limit:
            break
        if _parcel_geometry(parcel) is not None:
            continue
        pnu = parcel.get("pnu") or parcel.get("resolvedPnu")
        if not pnu:
            continue
        attempted += 1
        geometry = resolver(pnu)
        if isinstance(geometry, dict) and geometry.get("type") and geometry.get("coordinates"):
            parcel["geometry"] = geometry
            resolved += 1
    return resolved


def _extract_parcels(project_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Find the parcel list from the best available source.

    Priority: land ledger import (richest — area/지목/공시지가), then the flat
    land-evidence package, then the land-acquisition list (pnu/owner/area), then
    a top-level parcels list. Returns the first non-empty source.
    """
    for source in (
        project_data.get("land_ledger"),
        project_data.get("landLedger"),
        project_data.get("landEvidence"),
        project_data.get("land_evidence"),
        project_data,
    ):
        if isinstance(source, dict):
            parcels = source.get("parcels")
            if isinstance(parcels, list) and parcels:
                return [p for p in parcels if isinstance(p, dict)]
    for acquisition in (project_data.get("land_acquisition"), project_data.get("landAcquisition")):
        if isinstance(acquisition, list) and acquisition:
            return [p for p in acquisition if isinstance(p, dict)]
    return []


def _parcel_properties(parcel: Dict[str, Any]) -> Dict[str, Any]:
    owners = parcel.get("owners")
    owner_name = parcel.get("owner")
    if isinstance(owners, list) and owners and isinstance(owners[0], dict):
        owner_name = owners[0].get("ownerName") or owners[0].get("owner") or owner_name
    return {
        "pnu": parcel.get("pnu") or parcel.get("resolvedPnu"),
        "addressFragment": parcel.get("addressFragment") or parcel.get("address"),
        "lotNumber": parcel.get("lotNumber") or parcel.get("normalizedLotNumber") or parcel.get("parcelId"),
        "landCategory": parcel.get("landCategory"),
        "areaSqm": parcel.get("areaSqm") or parcel.get("cadastralArea") or parcel.get("area_sqm"),
        "areaPy": parcel.get("areaPy"),
        "officialLandPricePerSqm": parcel.get("officialLandPricePerSqm"),
        "officialLandPriceBaseYear": parcel.get("officialLandPriceBaseYear"),
        "owner": owner_name if isinstance(owner_name, str) else None,
    }


def _parcel_geometry(parcel: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return GeoJSON geometry if the parcel carries a resolved shape; else None.

    Accepts an embedded GeoJSON ``geometry`` (from VWorld/PostGIS) or a single
    representative point via ``centroid`` / ``lng``+``lat``. Never fabricated.
    """
    geometry = parcel.get("geometry")
    if isinstance(geometry, dict) and geometry.get("type") and geometry.get("coordinates"):
        return geometry
    centroid = parcel.get("centroid")
    if isinstance(centroid, (list, tuple)) and len(centroid) == 2 and all(_is_num(c) for c in centroid):
        return {"type": "Point", "coordinates": [float(centroid[0]), float(centroid[1])]}
    lng, lat = parcel.get("lng") or parcel.get("longitude"), parcel.get("lat") or parcel.get("latitude")
    if _is_num(lng) and _is_num(lat):
        return {"type": "Point", "coordinates": [float(lng), float(lat)]}
    return None


def _center_of(features: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    points: List[List[float]] = []
    for feature in features:
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            continue
        points.extend(_geometry_points(geometry))
    if not points:
        return None
    # Bounding-box midpoint — a stable initial map center, robust to how densely
    # a ring is sampled (a mean-of-vertices would skew toward repeated points).
    lngs = [p[0] for p in points]
    lats = [p[1] for p in points]
    return {"lng": (min(lngs) + max(lngs)) / 2, "lat": (min(lats) + max(lats)) / 2}


def _geometry_points(geometry: Dict[str, Any]) -> List[List[float]]:
    coords = geometry.get("coordinates")
    gtype = geometry.get("type")
    if gtype == "Point" and _coord_pair(coords):
        return [coords]
    points: List[List[float]] = []

    def walk(node: Any) -> None:
        if _coord_pair(node):
            points.append(node)
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    walk(coords)
    return points


def _coord_pair(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and _is_num(value[0])
        and _is_num(value[1])
    )


def _layers(
    *,
    located: int,
    priced: int,
    parcel_count: int,
    regulation: Dict[str, Any],
    risk_count: int,
) -> List[Dict[str, Any]]:
    has_zone = bool(regulation.get("zone") or regulation.get("useDistrict"))
    availability = {
        "parcel_boundary": ("available", located) if located else ("needs_geometry", 0),
        "official_land_price": ("available", priced) if priced else ("needs_data", 0),
        "zoning": ("available", 1) if has_zone else ("needs_data", 0),
        "transactions": ("available", None),  # served by the Valuemap endpoint
        "supply": ("planned", None),
        "risk": ("available", risk_count) if risk_count else ("needs_data", 0),
    }
    layers = []
    for layer_id, label, source in _LAYER_DEFS:
        status, count = availability.get(layer_id, ("planned", None))
        layers.append({
            "id": layer_id,
            "label": label,
            "source": source,
            "status": status,
            "featureCount": count,
        })
    return layers


def _data_limitations(parcel_count: int, located: int, priced: int) -> List[str]:
    limitations: List[str] = []
    if parcel_count == 0:
        limitations.append("토지대장/필지 데이터가 없어 지도 레이어를 구성할 수 없습니다.")
        return limitations
    if located == 0:
        limitations.append(
            f"{parcel_count}개 필지의 경계 형상이 아직 없습니다 — VWorld 지적도 연동(키) 또는 PostGIS land_parcels 적재 시 표시됩니다."
        )
    elif located < parcel_count:
        limitations.append(f"{parcel_count}개 중 {located}개 필지에만 형상이 있습니다.")
    if priced < parcel_count:
        limitations.append(f"{parcel_count}개 중 {priced}개 필지에만 공시지가가 있습니다.")
    return limitations


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
