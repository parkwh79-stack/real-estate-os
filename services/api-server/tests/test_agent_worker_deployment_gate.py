from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import run_agent_platform_worker


API_ROOT = Path(__file__).resolve().parents[1]
GATE_MESSAGE = "Agent Platform outbox worker is disabled in production/prod/staging"


@pytest.fixture
def worker_constructor(monkeypatch):
    constructor = Mock()
    monkeypatch.setitem(
        sys.modules,
        "agent_platform.operational",
        SimpleNamespace(AgentPlatformWorker=constructor),
    )
    return constructor


@pytest.mark.parametrize("environment", ["production", "prod", "staging", " Production "])
@pytest.mark.parametrize("arguments", [[], ["--once"]])
def test_hardened_agent_worker_cannot_construct_or_consume_jobs(
    monkeypatch, worker_constructor, environment, arguments
):
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", environment)
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("REALYTICS_DATABASE_URL", "postgresql://migration@test.invalid/app")
    monkeypatch.setenv("REALYTICS_PF_WORKER_DATABASE_URL", "postgresql://pf@test.invalid/app")
    monkeypatch.setattr(sys, "argv", ["run_agent_platform_worker.py", *arguments])

    with pytest.raises(SystemExit, match=GATE_MESSAGE):
        run_agent_platform_worker.main()

    worker_constructor.assert_not_called()
    worker_constructor.return_value.run_once.assert_not_called()
    worker_constructor.return_value.run_forever.assert_not_called()


def test_local_compatibility_opt_ins_cannot_bypass_hardened_gate(
    monkeypatch, worker_constructor
):
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", "staging")
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    for flag in (
        "REALYTICS_TEST_ISOLATION",
        "REALYTICS_ENABLE_LOCAL_AUTH",
        "REALYTICS_ENABLE_OAUTH_MOCKS",
        "REALYTICS_ENABLE_LEGACY_OAUTH_SESSIONS",
    ):
        monkeypatch.setenv(flag, "true")
    monkeypatch.setattr(sys, "argv", ["run_agent_platform_worker.py", "--once"])

    with pytest.raises(SystemExit, match=GATE_MESSAGE):
        run_agent_platform_worker.main()

    worker_constructor.assert_not_called()


@pytest.mark.parametrize("environment", ["development", "test"])
@pytest.mark.parametrize("once", [False, True])
def test_local_mocked_agent_worker_still_runs(
    monkeypatch, worker_constructor, capsys, environment, once
):
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", environment)
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    worker = worker_constructor.return_value
    worker.run_once.return_value = {"claimed": 2, "published": 2}
    args = [
        "run_agent_platform_worker.py",
        "--limit", "3",
        "--worker-id", "local-worker",
        "--poll-seconds", "0.2",
    ]
    if once:
        args.append("--once")
    monkeypatch.setattr(sys, "argv", args)

    run_agent_platform_worker.main()

    worker_constructor.assert_called_once_with(worker_id="local-worker")
    if once:
        worker.run_once.assert_called_once_with(limit=3)
        worker.run_forever.assert_not_called()
        assert "'claimed': 2" in capsys.readouterr().out
    else:
        worker.run_forever.assert_called_once_with(poll_seconds=0.2, limit=3)
        worker.run_once.assert_not_called()


@pytest.mark.parametrize("arguments, expected_code", [(["--help"], 0), (["--once"], 1)])
def test_direct_hardened_entrypoint_supports_help_but_rejects_work(
    arguments, expected_code
):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update({
        "REALYTICS_ENVIRONMENT": "production",
        "REALYTICS_STORAGE_BACKEND": "postgres",
        "REALYTICS_DATABASE_URL": "postgresql://unused@test.invalid/never-connect",
    })
    completed = subprocess.run(
        [sys.executable, str(API_ROOT / "scripts/run_agent_platform_worker.py"), *arguments],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == expected_code, completed.stderr
    if arguments == ["--help"]:
        assert "Available for local development and tests only" in " ".join(completed.stdout.split())
        assert "--allow" not in completed.stdout
        assert "--force" not in completed.stdout
    else:
        assert GATE_MESSAGE in completed.stderr
        assert "never-connect" not in completed.stderr
        assert "Traceback" not in completed.stderr
