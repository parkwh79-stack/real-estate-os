"""No-space cover addresses (e.g. a 사업계획서 표지 "경기도용인시기흥구상갈동13번지일대")
must normalize to a spaced, geocodable candidate — this is what unblocks Land-Legal
land-evidence auto-supplement (PNU/공시지가) when the parser stored a no-space address."""

from use_cases.intake_finalize import _address_candidates


def test_nospace_cover_address_normalizes_to_spaced_candidate():
    cands = _address_candidates("경기도용인시기흥구상갈동13번지일대")
    assert "경기도 용인시 기흥구 상갈동 13" in cands
    # dong-level fallback also present
    assert "경기도 용인시 기흥구 상갈동" in cands


def test_already_spaced_address_preserved():
    cands = _address_candidates("경기도 용인시 기흥구 상갈동 13")
    assert "경기도 용인시 기흥구 상갈동 13" in cands


def test_non_matching_text_returns_raw_only():
    cands = _address_candidates("주소 미상")
    assert cands == ["주소 미상"]
