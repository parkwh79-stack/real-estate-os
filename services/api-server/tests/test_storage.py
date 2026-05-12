from pathlib import Path

from storage import default_state, load_state, save_state


def test_json_state_storage_round_trip(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("REALYTICS_STATE_FILE", str(state_path))

    save_state(
        projects={"1": {"project": {"id": "1", "name": "Stored Project"}}},
        reports={"1": [{"report_id": "r-1"}]},
        orchestrations={"1": {"run-1": {"status": "completed"}}},
        project_id_counter=2,
    )

    state = load_state()

    assert state["project_id_counter"] == 2
    assert state["projects"]["1"]["project"]["name"] == "Stored Project"
    assert state["reports"]["1"][0]["report_id"] == "r-1"
    assert state["orchestrations"]["1"]["run-1"]["status"] == "completed"
    assert Path(state_path).exists()


def test_default_state_shape():
    state = default_state()

    assert state["projects"] == {}
    assert state["reports"] == {}
    assert state["orchestrations"] == {}
