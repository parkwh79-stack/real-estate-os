from __future__ import annotations

import io
import json

from fastapi.testclient import TestClient

from integrations.vworld import fetch_parcel_geometry_by_pnu, vworld_configured, vworld_status
from main import app
from use_cases.map_intelligence import build_project_map_intelligence, project_map_parcels, resolve_and_attach_geometry


_BOUNDARY = {
    "type": "Polygon",
    "coordinates": [[[128.55, 35.85], [128.56, 35.85], [128.56, 35.86], [128.55, 35.86], [128.55, 35.85]]],
}


def _project_with_parcels(geometry=None):
    parcel = {
        "pnu": "2729011700101230004",
        "addressFragment": "본리동 123-4",
        "lotNumber": "123-4",
        "landCategory": "대",
        "areaSqm": 521.0,
        "areaPy": 157.6,
        "officialLandPricePerSqm": 3150000,
        "officialLandPriceBaseYear": "2026",
        "owners": [{"ownerName": "홍길동"}],
    }
    if geometry is not None:
        parcel["geometry"] = geometry
    return {"land_ledger": {"parcels": [parcel]}, "regulation": {"zone": "일반상업지역", "applied_far": 760}}


def test_unlocated_parcel_is_valid_feature_with_null_geometry():
    result = build_project_map_intelligence(_project_with_parcels())
    feature_collection = result["parcelGeoJson"]
    assert feature_collection["type"] == "FeatureCollection"
    assert len(feature_collection["features"]) == 1
    feature = feature_collection["features"][0]
    assert feature["geometry"] is None
    assert feature["properties"]["pnu"] == "2729011700101230004"
    assert feature["properties"]["officialLandPricePerSqm"] == 3150000
    assert result["summary"]["hasGeometry"] is False
    assert result["center"] is None

    layers = {layer["id"]: layer for layer in result["layers"]}
    assert layers["parcel_boundary"]["status"] == "needs_geometry"
    assert layers["official_land_price"]["status"] == "available"
    assert layers["zoning"]["status"] == "available"
    assert any("경계 도형" in note for note in result["dataLimitations"])


def test_polygon_geometry_makes_layer_available_and_centers():
    result = build_project_map_intelligence(_project_with_parcels(geometry=_BOUNDARY))
    feature = result["parcelGeoJson"]["features"][0]
    assert feature["geometry"]["type"] == "Polygon"
    assert result["summary"]["hasGeometry"] is True
    assert result["summary"]["boundaryParcelCount"] == 1
    assert result["center"]["lng"] == 128.555
    layers = {layer["id"]: layer for layer in result["layers"]}
    assert layers["parcel_boundary"]["status"] == "available"
    assert layers["parcel_boundary"]["featureCount"] == 1


def test_point_centroid_is_accepted():
    parcel_project = _project_with_parcels()
    parcel_project["land_ledger"]["parcels"][0]["centroid"] = [128.6, 35.9]
    result = build_project_map_intelligence(parcel_project)
    feature = result["parcelGeoJson"]["features"][0]
    assert feature["geometry"] == {"type": "Point", "coordinates": [128.6, 35.9]}
    assert feature["properties"]["geometryStatus"] == "point"
    assert result["center"] == {"lng": 128.6, "lat": 35.9}
    assert result["summary"]["boundaryParcelCount"] == 0
    assert result["summary"]["pointParcelCount"] == 1
    assert result["layers"][0]["status"] == "needs_geometry"


def test_land_acquisition_list_is_a_parcel_source():
    project = {
        "land_acquisition": [
            {"parcelId": "P-001", "pnu": "2729011700101230004", "owner": "Seller A", "area_sqm": 5000.0},
        ],
    }
    result = build_project_map_intelligence(project)
    assert result["summary"]["parcelCount"] == 1
    props = result["parcelGeoJson"]["features"][0]["properties"]
    assert props["pnu"] == "2729011700101230004"
    assert props["areaSqm"] == 5000.0
    assert props["owner"] == "Seller A"
    assert props["lotNumber"] == "P-001"


def test_no_parcels_reports_limitation():
    result = build_project_map_intelligence({})
    assert result["summary"]["parcelCount"] == 0
    assert result["parcelGeoJson"]["features"] == []
    assert any("필지 데이터" in note for note in result["dataLimitations"])


def test_map_endpoint_returns_payload():
    client = TestClient(app)
    project_id = client.get("/projects/").json()[0]["project"]["id"]
    res = client.get(f"/projects/{project_id}/map")
    assert res.status_code == 200
    body = res.json()
    assert body["schemaVersion"] == "realytics-map-intelligence-v1"
    assert body["parcelGeoJson"]["type"] == "FeatureCollection"
    assert isinstance(body["layers"], list) and len(body["layers"]) == 6
    assert {layer["label"] for layer in body["layers"]} >= {"필지 경계", "공시지가", "용도지역"}


def test_map_endpoint_404_for_unknown_project():
    client = TestClient(app)
    assert client.get("/projects/__nope__/map").status_code == 404


def test_resolve_and_attach_geometry_fills_missing_only():
    project = _project_with_parcels()
    parcels = project_map_parcels(project)
    calls = []

    def resolver(pnu):
        calls.append(pnu)
        return _BOUNDARY

    resolved = resolve_and_attach_geometry(parcels, resolver, limit=30)
    assert resolved == 1
    assert calls == ["2729011700101230004"]
    result = build_project_map_intelligence(project, parcels=parcels)
    assert result["summary"]["hasGeometry"] is True
    assert resolve_and_attach_geometry(parcels, resolver, limit=30) == 0


def test_resolve_skips_when_resolver_returns_none():
    project = _project_with_parcels()
    parcels = project_map_parcels(project)
    resolved = resolve_and_attach_geometry(parcels, lambda pnu: None, limit=30)
    assert resolved == 0
    assert build_project_map_intelligence(project, parcels=parcels)["summary"]["hasGeometry"] is False


def test_boundary_lookup_upgrades_point_and_leaves_saved_parcel_unchanged():
    point = {"type": "Point", "coordinates": [128.6, 35.9]}
    project = _project_with_parcels(geometry=point)
    parcels = project_map_parcels(project)
    assert resolve_and_attach_geometry(parcels, lambda pnu: point) == 0
    assert parcels[0]["geometry"] == point
    assert resolve_and_attach_geometry(parcels, lambda pnu: _BOUNDARY) == 1
    feature = build_project_map_intelligence(project, parcels=parcels)["parcelGeoJson"]["features"][0]
    assert feature["geometry"] == _BOUNDARY
    assert feature["properties"]["geometryStatus"] == "boundary"
    assert project["land_ledger"]["parcels"][0]["geometry"] == point


def test_canonical_map_merges_parcel_sources_and_resolves_only_a_valid_focused_pnu():
    first_pnu = "2729011700101230004"
    focus_pnu = "2729011700101230005"
    wrong_pnu = "2729011700101230007"
    project = {
        "land": {"address": "대구 달서구 본리동 123-4"},
        "landLedger": {"parcels": [{"lotNumber": "123-4"}, {"lotNumber": "123-6", "pnu": wrong_pnu}]},
        "land_acquisition": [{"lotNumber": "123-4", "PNU": first_pnu}, {"lotNumber": "123-5", "pnu": focus_pnu}],
    }
    calls = []

    def resolver(pnu):
        calls.append(pnu)
        return _BOUNDARY

    parcels = project_map_parcels(project)
    assert resolve_and_attach_geometry(parcels, resolver, limit=1, focus_pnu=focus_pnu) == 1
    assert resolve_and_attach_geometry(parcels, resolver, limit=3, focus_pnu=focus_pnu) == 0
    assert resolve_and_attach_geometry(parcels, resolver, limit=3, focus_pnu=wrong_pnu) == 0
    assert calls == [focus_pnu]
    assert resolve_and_attach_geometry(parcels, resolver, limit=3) == 1
    assert calls == [focus_pnu, first_pnu]
    features = build_project_map_intelligence(project, parcels=parcels)["parcelGeoJson"]["features"]
    assert [feature["properties"]["pnu"] for feature in features] == [first_pnu, None, focus_pnu]
    assert [feature["properties"]["geometryStatus"] for feature in features] == ["boundary", "unlocated", "boundary"]


def test_resolve_limit_can_cover_large_multi_parcel_map():
    project = {
        "land_ledger": {
            "parcels": [
                {"pnu": f"41463103001{index:04d}0000", "addressFragment": f"상갈동 {index}", "lotNumber": str(index)}
                for index in range(1, 113)
            ]
        }
    }
    calls = []

    def resolver(pnu):
        calls.append(pnu)
        return _BOUNDARY

    parcels = project_map_parcels(project)
    assert resolve_and_attach_geometry(parcels, resolver, limit=150) == 112
    assert len(calls) == 112
    result = build_project_map_intelligence(project, parcels=parcels)
    assert result["summary"]["boundaryParcelCount"] == 112


def test_vworld_status_is_secret_safe():
    status = vworld_status({"VWORLD_API_KEY": "SECRET-KEY-VALUE"})
    assert status["configured"] is True
    assert "SECRET-KEY-VALUE" not in str(status)
    assert vworld_configured({}) is False


def test_vworld_geometry_requires_matching_pnu_and_polygon(monkeypatch):
    pnu = "2729011700101230004"
    features = [
        {"properties": {"pnu": "2729011700101230005"}, "geometry": _BOUNDARY},
        {"geometry": _BOUNDARY},
        {"properties": {"pnu": pnu}, "geometry": {"type": "Point", "coordinates": [128.6, 35.9]}},
    ]

    def respond(url, timeout):
        payload = {"response": {"status": "OK", "result": {"featureCollection": {"features": features}}}}
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr("integrations.vworld.urllib.request.urlopen", respond)
    environ = {"VWORLD_API_KEY": "test-only-key"}
    geometry, diagnosis = fetch_parcel_geometry_by_pnu(pnu, environ=environ)
    assert geometry is None
    assert diagnosis["status"] == "not_found"

    features.append({"properties": {"pnu": pnu}, "geometry": _BOUNDARY})
    geometry, diagnosis = fetch_parcel_geometry_by_pnu(pnu, environ=environ)
    assert geometry == _BOUNDARY
    assert diagnosis["status"] == "resolved"
