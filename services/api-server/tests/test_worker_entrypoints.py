from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from scripts.run_pf_review_worker import repository_for_job


API_ROOT = Path(__file__).resolve().parents[1]


def test_agent_platform_worker_supports_documented_direct_invocation() -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [
            sys.executable,
            str(API_ROOT / "scripts" / "run_agent_platform_worker.py"),
            "--help",
        ],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "REALYTICS Agent Platform outbox worker" in completed.stdout


def test_pf_review_worker_repository_is_bound_to_job_tenant(monkeypatch) -> None:
    captured = {}

    def fake_repository(*, tenant_id):
        captured["tenantId"] = tenant_id
        return object()

    monkeypatch.setattr(
        "scripts.run_pf_review_worker.orchestration_repository",
        fake_repository,
    )

    repository_for_job({"jobId": "job-1", "tenantId": "tenant-a"})

    assert captured == {"tenantId": "tenant-a"}
