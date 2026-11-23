from __future__ import annotations

import subprocess
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]


def test_agent_platform_worker_supports_direct_invocation_from_api_root() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/run_agent_platform_worker.py", "--help"],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Run the REALYTICS Agent Platform outbox worker." in result.stdout
