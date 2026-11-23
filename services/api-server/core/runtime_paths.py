"""One configurable local artifact root; production defaults stay unchanged."""
from __future__ import annotations

import os
from pathlib import Path


def runtime_data_dir() -> Path:
    configured = os.environ.get("REALYTICS_RUNTIME_DIR")
    return (
        Path(configured).expanduser().resolve()
        if configured
        else Path(__file__).resolve().parents[1] / "data" / "runtime"
    )
