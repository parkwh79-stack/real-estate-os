from use_cases.parcel_pnu_resolution import (
    _address_prefix,
    _candidate_address,
    _pnu_lookup_to_result,
    resolve_project_parcels_use_case,
)


class _FakeRepo:
    def __init__(self, project):
        self.project = project
        self.updates = []

    def get(self, pid):
        return self.project

    def update(self, pid, payload):
        self.updates.append(payload)
        self.project = {**self.project, **payload}
        return self.project


def _resolver(address):
    table = {
        "경기도 용인시 기흥구 상갈동 13": ("4146310300100130000", 1466000.0),
        "경기도 용인시 기흥구 상갈동 14-2": ("4146310300100140002", 1350000.0),
        "경기도 용인시 기흥구 보라동 62": ("4146310400100620000", 990000.0),
    }
    if address in table:
        pnu, jiga = table[address]
        return {
            "status": "resolved",
            "pnu": pnu,
            "officialLandPricePerSqm": jiga,
            "officialLandPriceBaseYear": "2025",
        }
    return {"status": "parcel_unresolved"}


def test_address_prefix_extraction():
    assert _address_prefix("경기도 용인시 기흥구 상갈동 13") == "경기도 용인시 기흥구 상갈동"
    assert _address_prefix("대구광역시 달서구 본동 743 일원") == "대구광역시 달서구 본동"
    assert _address_prefix("") == ""


def test_candidate_address_uses_prefix_and_swaps_dong():
    prefix = "경기도 용인시 기흥구 상갈동"
    assert _candidate_address({"lotNumber": "14-2"}, prefix) == "경기도 용인시 기흥구 상갈동 14-2"
    # 다른 동 필지는 prefix의 동을 교체
    assert (
        _candidate_address({"dong": "보라동", "lotNumber": "62"}, prefix)
        == "경기도 용인시 기흥구 보라동 62"
    )


def test_batch_resolves_each_parcel_with_provenance():
    repo = _FakeRepo(
        {
            "land": {"address": "경기도 용인시 기흥구 상갈동 13"},
            "land_acquisition": [
                {"addressFragment": "상갈동 13", "dong": "상갈동", "lotNumber": "13"},
                {"addressFragment": "상갈동 14-2", "dong": "상갈동", "lotNumber": "14-2"},
                {"addressFragment": "상갈동 9999", "dong": "상갈동", "lotNumber": "9999"},
            ],
        }
    )

    result = resolve_project_parcels_use_case(repo, "77", resolver=_resolver)

    assert result["resolvedCount"] == 2
    statuses = [row["status"] for row in result["results"]]
    assert statuses == ["resolved", "resolved", "unresolved"]
    parcels = repo.project["land_acquisition"]
    assert parcels[0]["pnu"] == "4146310300100130000"
    assert parcels[1]["pnu"] == "4146310300100140002"
    assert parcels[1]["officialLandPricePerSqm"] == 1350000.0
    assert parcels[1]["pnuResolution"]["status"] == "resolved"
    # 실패 필지는 시도 주소·사유가 남고 PNU는 지어내지 않음
    assert "pnu" not in parcels[2]
    assert parcels[2]["pnuResolution"]["status"] == "unresolved"
    assert repo.updates, "must persist once"


def test_batch_skips_already_resolved_and_respects_limit():
    repo = _FakeRepo(
        {
            "land": {"address": "경기도 용인시 기흥구 상갈동 13"},
            "land_acquisition": [
                {"addressFragment": "상갈동 13", "lotNumber": "13", "pnu": "existing"},
                {"addressFragment": "상갈동 14-2", "lotNumber": "14-2"},
                {"addressFragment": "보라동 62", "dong": "보라동", "lotNumber": "62"},
            ],
        }
    )

    result = resolve_project_parcels_use_case(repo, "77", resolver=_resolver, limit=1)

    assert result["results"][0]["status"] == "already_resolved"
    assert result["attemptedCount"] == 1
    assert result["results"][2]["status"] == "skipped_limit"


def test_lot_mismatch_rejects_wrong_parcel_pnu():
    """지오코더가 동 단위로 폴백해 이웃 필지를 반환하면 PNU를 붙이지 않는다."""

    def _fallback_resolver(address):
        # 어떤 지번을 물어도 상갈동 1번지 필지를 반환하는 잘못된 폴백 상황
        return {
            "status": "resolved",
            "pnu": "4146310300100010000",
            "lotNumber": "1",
            "officialLandPricePerSqm": 127600.0,
        }

    repo = _FakeRepo(
        {
            "land": {"address": "경기도 용인시 기흥구 상갈동 13"},
            "land_acquisition": [{"addressFragment": "상갈동 99999", "dong": "상갈동", "lotNumber": "99999"}],
        }
    )

    result = resolve_project_parcels_use_case(repo, "77", resolver=_fallback_resolver)

    row = result["results"][0]
    assert row["status"] == "unresolved"
    assert "lot_mismatch" in row["reason"]
    assert "pnu" not in repo.project["land_acquisition"][0]


def test_matching_lot_is_verified():
    def _resolver_with_lot(address):
        return {
            "status": "resolved",
            "pnu": "4146310300100130000",
            "lotNumber": "13",
            "officialLandPricePerSqm": 1466000.0,
        }

    repo = _FakeRepo(
        {
            "land": {"address": "경기도 용인시 기흥구 상갈동 13"},
            "land_acquisition": [{"addressFragment": "상갈동 13", "lotNumber": "13"}],
        }
    )

    result = resolve_project_parcels_use_case(repo, "77", resolver=_resolver_with_lot)

    assert result["resolvedCount"] == 1
    assert repo.project["land_acquisition"][0]["pnuResolution"]["lotVerification"] == "verified"


def test_duplicate_parcel_in_both_lists_resolved_once_and_synced():
    """같은 필지가 land_acquisition과 landLedger.parcels 양쪽에 있어도 1회만 시도하고
    결과를 양쪽에 반영한다 (통합 리허설에서 발견한 이중 카운팅 버그 회귀 방지)."""
    shared = {"addressFragment": "본리 1-3", "dong": "본리동", "lotNumber": "1-3"}
    repo = _FakeRepo(
        {
            "land": {"address": "대구광역시 달서구 본리동 1-3"},
            "land_acquisition": [dict(shared)],
            "landLedger": {"parcels": [dict(shared)]},
        }
    )

    def _r(address):
        return {"status": "resolved", "pnu": "2729011400100010003", "lotNumber": "1-3", "officialLandPricePerSqm": 3216000.0}

    result = resolve_project_parcels_use_case(repo, "99", resolver=_r)

    # One physical parcel -> counted once, attempted once.
    assert result["totalParcels"] == 1
    assert result["attemptedCount"] == 1
    assert result["resolvedCount"] == 1
    assert len(result["results"]) == 1
    # Both list copies received the PNU.
    assert repo.project["land_acquisition"][0]["pnu"] == "2729011400100010003"
    assert repo.project["landLedger"]["parcels"][0]["pnu"] == "2729011400100010003"


def test_pnu_construction_precise_resolution_and_honest_not_found():
    """정밀 해석: 법정동코드+지번으로 PNU를 구성해 VWorld에서 확인.
    현 지적도에 없는 지번(재개발 합병 등)은 조작 없이 not_in_current_cadastre."""
    repo = _FakeRepo(
        {
            "land": {"address": "대구광역시 달서구 본리동 1-3"},
            "land_acquisition": [
                {"addressFragment": "본리 1-3", "dong": "본리동", "lotNumber": "1-3"},
                {"addressFragment": "본리 1-4", "dong": "본리동", "lotNumber": "1-4"},
            ],
        }
    )

    # VWorld cadastre only has 1-3 (…0003); 1-4 (…0004) was merged/renumbered.
    cadastre = {
        "2729011400100010003": {"pnu": "2729011400100010003", "jibun": "1-3", "jiga": "3216000"},
    }

    def _dong_code(addr):
        return "2729011400"

    def _lookup(pnu):
        props = cadastre.get(pnu)
        if props:
            return _pnu_lookup_to_result(props)
        return {"status": "not_found"}

    result = resolve_project_parcels_use_case(
        repo, "1", dong_code_resolver=_dong_code, pnu_lookup=_lookup
    )

    assert result["resolvedCount"] == 1
    by_lot = {r["parcel"]: r for r in result["results"]}
    assert by_lot["본리 1-3"]["status"] == "resolved"
    assert by_lot["본리 1-3"]["pnu"] == "2729011400100010003"
    assert by_lot["본리 1-3"]["method"] == "pnu_construction"
    assert by_lot["본리 1-4"]["status"] == "unresolved"
    assert by_lot["본리 1-4"]["reason"] == "not_in_current_cadastre"
    assert by_lot["본리 1-4"]["constructedPnu"] == "2729011400100010004"
    # Resolved parcel got PNU + jiga; never a fabricated neighbor PNU.
    p13 = repo.project["land_acquisition"][0]
    assert p13["pnu"] == "2729011400100010003"
    assert p13["officialLandPricePerSqm"] == 3216000.0
    assert p13["pnuResolution"]["lotVerification"] == "verified"
    assert "pnu" not in repo.project["land_acquisition"][1]


def test_pnu_construction_falls_back_to_address_when_no_dong_code():
    """법정동코드를 얻지 못하면 주소 resolver 폴백."""
    repo = _FakeRepo(
        {
            "land": {"address": "경기도 용인시 기흥구 상갈동 13"},
            "land_acquisition": [{"addressFragment": "상갈동 13", "lotNumber": "13"}],
        }
    )
    calls = {"resolver": 0}

    def _resolver(address):
        calls["resolver"] += 1
        return {"status": "resolved", "pnu": "4146310300100130000", "lotNumber": "13", "officialLandPricePerSqm": 1466000.0}

    result = resolve_project_parcels_use_case(
        repo, "1", resolver=_resolver, dong_code_resolver=lambda a: None, pnu_lookup=lambda p: {"status": "not_found"}
    )

    assert calls["resolver"] == 1  # fell back to address resolver
    assert result["resolvedCount"] == 1
