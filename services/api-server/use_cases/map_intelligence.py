from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

MAP_INTELLIGENCE_SCHEMA_VERSION = "realytics-map-intelligence-v1"

# Six-layer spatial OS. All layers are derived from actual stored data or live
# connector output. Parcel geometry is never fabricated: parcels without a real
# VWorld/PostGIS shape remain unlocated GeoJSON features.
_LAYER_DEFS = [
    ("parcel_boundary", "필지 경계", "VWorld 지적도 / PostGIS land_parcels"),
    ("official_land_price", "공시지가", "공공데이터포털 / VWorld / 수동 증빙"),
    ("zoning", "용도지역", "VWorld 토지이용계획"),
    ("transactions", "실거래·시세", "국토부 실거래가 / Valuemap"),
    ("supply", "주변 공급", "분양/공급 데이터"),
    ("risk", "리스크", "Risk Register"),
]


def build_project_map_intelligence(project_data: Dict[str, Any]) -> Dict[str, Any]:
    """Build project-level spatial intelligence as GeoJSON + layer readiness."""

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
        features.append(
            {
                "type": "Feature",
                "id": properties.get("pnu") or properties.get("addressFragment") or f"parcel-{index}",
                "geometry": geometry,
                "properties": properties,
            }
        )

    feature_collection = {"type": "FeatureCollection", "features": features}
    center = _center_of(features)
    regulation = project_data.get("regulation") or {}
    risk_register = project_data.get("riskRegister") or {}
    risk_rows = risk_register.get("risks") if isinstance(risk_register, dict) else None
    signals = project_data.get("_layerSignals") if isinstance(project_data.get("_layerSignals"), dict) else None

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
        "layers": _layers(
            located=located,
            priced=priced,
            parcel_count=len(parcels),
            regulation=regulation,
            risk_count=len(risk_rows) if isinstance(risk_rows, list) else 0,
            signals=signals,
        ),
        "dataLimitations": _data_limitations(len(parcels), located, priced),
    }


def resolve_and_attach_geometry(
    project_data: Dict[str, Any],
    resolver,
    *,
    limit: int = 50,
) -> int:
    """Attach real parcel boundary geometry to missing parcels in-place.

    ``resolver(pnu) -> geometry|None`` is injected so the function stays
    testable. Only parcels with a PNU and no existing geometry are attempted.
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
    """Find parcels from the richest available source."""

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
                return [parcel for parcel in parcels if isinstance(parcel, dict)]

    for acquisition in (project_data.get("land_acquisition"), project_data.get("landAcquisition")):
        if isinstance(acquisition, list) and acquisition:
            return [parcel for parcel in acquisition if isinstance(parcel, dict)]
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
    geometry = parcel.get("geometry")
    if isinstance(geometry, dict) and geometry.get("type") and geometry.get("coordinates"):
        return geometry
    centroid = parcel.get("centroid")
    if isinstance(centroid, (list, tuple)) and len(centroid) == 2 and all(_is_num(value) for value in centroid):
        return {"type": "Point", "coordinates": [float(centroid[0]), float(centroid[1])]}
    lng = parcel.get("lng") or parcel.get("longitude")
    lat = parcel.get("lat") or parcel.get("latitude")
    if _is_num(lng) and _is_num(lat):
        return {"type": "Point", "coordinates": [float(lng), float(lat)]}
    return None


def _center_of(features: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    points: List[List[float]] = []
    for feature in features:
        geometry = feature.get("geometry")
        if isinstance(geometry, dict):
            points.extend(_geometry_points(geometry))
    if not points:
        return None
    lngs = [point[0] for point in points]
    lats = [point[1] for point in points]
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
    signals: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    has_zone = bool(regulation.get("zone") or regulation.get("useDistrict"))
    availability = {
        "parcel_boundary": ("available", located) if located else ("needs_geometry", 0),
        "official_land_price": ("available", priced) if priced else ("needs_data", 0),
        "zoning": ("available", 1) if has_zone else ("needs_data", 0),
        "transactions": ("available", None),
        "supply": ("planned", None),
        "risk": ("available", risk_count) if risk_count else ("needs_data", 0),
    }

    signals = signals or {}
    layers = []
    for layer_id, label, source in _LAYER_DEFS:
        status, count = availability.get(layer_id, ("planned", None))
        detail = None
        source_label = source
        signal = signals.get(layer_id)
        if isinstance(signal, dict):
            if signal.get("status"):
                status = signal["status"]
            if "featureCount" in signal:
                count = signal["featureCount"]
            detail = signal.get("detail")
            source_label = signal.get("source") or source
        layers.append(
            {
                "id": layer_id,
                "label": label,
                "source": source_label,
                "status": status,
                "featureCount": count,
                "detail": detail,
            }
        )
    return layers


def _data_limitations(parcel_count: int, located: int, priced: int) -> List[str]:
    limitations: List[str] = []
    if parcel_count == 0:
        limitations.append("저장된 필지 데이터가 없어 지도 레이어를 구성할 수 없습니다.")
        return limitations
    if located == 0:
        limitations.append(
            f"{parcel_count}개 필지의 경계 도형이 아직 없습니다. VWorld 지적도 연동 또는 PostGIS land_parcels 적재 후 표시됩니다."
        )
    elif located < parcel_count:
        limitations.append(f"{parcel_count}개 중 {located}개 필지에만 경계 도형이 있습니다.")
    if priced < parcel_count:
        limitations.append(f"{parcel_count}개 중 {priced}개 필지에만 공시지가가 있습니다.")
    return limitations


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
