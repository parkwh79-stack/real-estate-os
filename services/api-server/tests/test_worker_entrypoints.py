from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import pytest

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


def test_local_pf_review_worker_repository_is_bound_to_job_tenant(monkeypatch) -> None:
    captured = {}

    def fake_repository(*, tenant_id):
        captured["tenantId"] = tenant_id
        return object()

    monkeypatch.setattr(
        "routers.projects.orchestration_repository",
        fake_repository,
    )
    monkeypatch.setattr("storage.storage_backend", lambda: "sqlite")

    repository_for_job({"jobId": "job-1", "tenantId": "tenant-a"})

    assert captured == {"tenantId": "tenant-a"}


def test_postgres_pf_review_repositories_share_job_tenant_and_worker_pool(monkeypatch) -> None:
    from db.postgres_runtime_pool import pf_worker_tenant_transaction
    from db.repositories.orchestration_repository import PostgresOrchestrationRepository

    monkeypatch.setattr("storage.storage_backend", lambda: "postgres")
    repository = repository_for_job({"jobId": "job-1", "tenantId": "tenant-a"})

    assert isinstance(repository, PostgresOrchestrationRepository)
    for bound in (repository, repository.report_repository, repository.project_snapshot_repository):
        assert bound.tenant_id == "tenant-a"
        assert bound._transaction is pf_worker_tenant_transaction


@pytest.mark.parametrize("job", [{}, {"tenantId": " "}])
def test_pf_review_repository_rejects_missing_job_tenant(job) -> None:
    with pytest.raises(RuntimeError, match="persisted tenantId"):
        repository_for_job(job)
