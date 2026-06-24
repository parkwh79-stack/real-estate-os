from __future__ import annotations

from fastapi.testclient import TestClient

from main import app
from use_cases.map_intelligence import build_project_map_intelligence, resolve_and_attach_geometry
from integrations.vworld import vworld_configured, vworld_status


def _project_with_parcels(geometry=None):
    parcel = {
        "pnu": "2729011700101230004",
        "addressFragment": "본리동 1230-4",
        "lotNumber": "1230-4",
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
    fc = result["parcelGeoJson"]
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 1
    feature = fc["features"][0]
    assert feature["geometry"] is None  # not fabricated
    assert feature["properties"]["pnu"] == "2729011700101230004"
    assert feature["properties"]["officialLandPricePerSqm"] == 3150000
    assert result["summary"]["hasGeometry"] is False
    assert result["center"] is None
    # boundary layer waits for geometry; price/zoning are live
    layers = {layer["id"]: layer for layer in result["layers"]}
    assert layers["parcel_boundary"]["status"] == "needs_geometry"
    assert layers["official_land_price"]["status"] == "available"
    assert layers["zoning"]["status"] == "available"
    assert any("경계 형상" in note for note in result["dataLimitations"])


def test_polygon_geometry_makes_layer_available_and_centers():
    geometry = {
        "type": "Polygon",
        "coordinates": [[[128.55, 35.85], [128.56, 35.85], [128.56, 35.86], [128.55, 35.86], [128.55, 35.85]]],
    }
    result = build_project_map_intelligence(_project_with_parcels(geometry=geometry))
    feature = result["parcelGeoJson"]["features"][0]
    assert feature["geometry"]["type"] == "Polygon"
    assert result["summary"]["hasGeometry"] is True
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
    assert result["center"] == {"lng": 128.6, "lat": 35.9}


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
    assert any("토지대장" in note for note in result["dataLimitations"])


def test_map_endpoint_returns_payload():
    client = TestClient(app)
    project_id = client.get("/projects/").json()[0]["project"]["id"]
    res = client.get(f"/projects/{project_id}/map")
    assert res.status_code == 200
    body = res.json()
    assert body["schemaVersion"] == "realytics-map-intelligence-v1"
    assert body["parcelGeoJson"]["type"] == "FeatureCollection"
    assert isinstance(body["layers"], list) and len(body["layers"]) == 6


def test_map_endpoint_404_for_unknown_project():
    client = TestClient(app)
    assert client.get("/projects/__nope__/map").status_code == 404


def test_resolve_and_attach_geometry_fills_missing_only():
    project = _project_with_parcels()
    poly = {"type": "Polygon", "coordinates": [[[128.5, 35.8], [128.6, 35.8], [128.6, 35.9], [128.5, 35.8]]]}
    calls = []

    def resolver(pnu):
        calls.append(pnu)
        return poly

    resolved = resolve_and_attach_geometry(project, resolver, limit=30)
    assert resolved == 1
    assert calls == ["2729011700101230004"]
    result = build_project_map_intelligence(project)
    assert result["summary"]["hasGeometry"] is True
    # a second pass resolves nothing (already has geometry)
    assert resolve_and_attach_geometry(project, resolver, limit=30) == 0


def test_resolve_skips_when_resolver_returns_none():
    project = _project_with_parcels()
    resolved = resolve_and_attach_geometry(project, lambda pnu: None, limit=30)
    assert resolved == 0
    assert build_project_map_intelligence(project)["summary"]["hasGeometry"] is False


def test_vworld_status_is_secret_safe():
    status = vworld_status({"VWORLD_API_KEY": "SECRET-KEY-VALUE"})
    assert status["configured"] is True
    # the key itself is never echoed
    assert "SECRET-KEY-VALUE" not in str(status)
    assert vworld_configured({}) is False
