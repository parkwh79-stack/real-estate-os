from use_cases import intake_finalize
from use_cases.intake_finalize import finalize_project_intake_use_case, resolve_address_to_parcel


class _FakeRepo:
    def __init__(self, project):
        self.project = project

    def get(self, pid):
        return self.project

    def update(self, pid, payload):
        merged = {**self.project}
        for k, v in payload.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k] = {**merged[k], **v}
            else:
                merged[k] = v
        self.project = merged
        return self.project


def test_resolve_no_address_returns_no_address():
    assert resolve_address_to_parcel("")["status"] == "no_address"


def test_finalize_no_address_does_not_persist(monkeypatch):
    import use_cases.address_mining as mining

    monkeypatch.setattr(mining, "mine_project_address", lambda pid, p, **kw: None)
    repo = _FakeRepo({"project": {"id": "1"}, "land": {}})
    result = finalize_project_intake_use_case(repo, "1")
    assert result["persisted"] is False
    assert result["parcel"]["status"] == "no_address"
    assert result["intakeReadinessPlan"]["track"] == "development"
    assert result["intakeReadinessPlan"]["trackWorkflow"]["label"] == "부동산 개발"


def test_finalize_returns_selected_track_workflow(monkeypatch):
    import use_cases.address_mining as mining

    monkeypatch.setattr(mining, "mine_project_address", lambda pid, p, **kw: None)
    repo = _FakeRepo({"project": {"id": "2"}, "land": {}})
    result = finalize_project_intake_use_case(repo, "2", track="pf")
    plan = result["intakeReadinessPlan"]
    assert plan["track"] == "pf"
    assert plan["trackWorkflow"]["label"] == "부동산 PF"
    step_keys = {step["key"] for step in plan["trackWorkflow"]["steps"]}
    assert {"source_reconciliation", "waterfall_recovery", "credit_decision"} <= step_keys


def test_finalize_saves_pnu_when_resolved(monkeypatch):
    monkeypatch.setattr(
        intake_finalize,
        "resolve_address_to_parcel",
        lambda address, environ=None: {
            "status": "resolved",
            "pnu": "2729012400110000000",
            "center": {"lng": 128.5, "lat": 35.8},
            "officialLandPricePerSqm": 1135000.0,
            "officialLandPriceBaseYear": "2025",
        },
    )
    repo = _FakeRepo({"project": {"id": "9"}, "land": {"address": "대구광역시 달서구 본동 743"}})
    result = finalize_project_intake_use_case(repo, "9")
    assert result["persisted"] is True
    assert repo.project["land"]["pnu"] == "2729012400110000000"
    assert repo.project["land"]["officialLandPricePerSqm"] == 1135000.0
    assert repo.project["land"]["center"]["lng"] == 128.5


def test_finalize_falls_back_to_parcel_address_fragment(monkeypatch):
    captured = {}

    def _fake(address, environ=None):
        captured["address"] = address
        return {"status": "parcel_unresolved"}

    monkeypatch.setattr(intake_finalize, "resolve_address_to_parcel", _fake)
    repo = _FakeRepo({"project": {"id": "3"}, "land": {}, "land_acquisition": [{"addressFragment": "용인 상갈동 13"}]})
    finalize_project_intake_use_case(repo, "3")
    assert captured["address"] == "용인 상갈동 13"
