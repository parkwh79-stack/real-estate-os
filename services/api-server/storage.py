import json
import os
from pathlib import Path
from typing import Any, Dict


STATE_VERSION = 1
DEFAULT_STATE_FILE = Path(__file__).parent / "data" / "runtime" / "state.json"


def state_file_path() -> Path:
    configured = os.environ.get("REALYTICS_STATE_FILE")
    return Path(configured) if configured else DEFAULT_STATE_FILE


def default_state() -> Dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "project_id_counter": 1,
        "projects": {},
        "reports": {},
        "orchestrations": {},
    }


def load_state() -> Dict[str, Any] | None:
    path = state_file_path()
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        state = json.load(f)
    state.setdefault("version", STATE_VERSION)
    state.setdefault("project_id_counter", 1)
    state.setdefault("projects", {})
    state.setdefault("reports", {})
    state.setdefault("orchestrations", {})
    return state


def save_state(
    projects: Dict[str, Any],
    reports: Dict[str, Any],
    orchestrations: Dict[str, Any],
    project_id_counter: int,
) -> None:
    path = state_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    payload = {
        "version": STATE_VERSION,
        "project_id_counter": project_id_counter,
        "projects": projects,
        "reports": reports,
        "orchestrations": orchestrations,
    }
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp_path.replace(path)
