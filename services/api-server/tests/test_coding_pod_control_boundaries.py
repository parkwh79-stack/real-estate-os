import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from agent_platform.coding import remote_executor
from scripts import run_coding_pod_worker


def _job():
    return SimpleNamespace(job_id="offline-job", tenant_id="test-tenant", workspace_ref="test-repo",
        branch_name="codex/offline", requested_change="offline test", allowed_paths=["tests"], attempt_count=1)


@pytest.mark.parametrize("endpoint", ["https:///missing-host", "https://user:secret@example.test", "https://example.test?q=secret", "https://example.test#fragment", "http://external.test"])
def test_executor_rejects_ambiguous_or_credential_bearing_endpoints(endpoint):
    with pytest.raises(remote_executor.RemoteCodingExecutorError):
        remote_executor.RemoteIsolatedCodingExecutor(endpoint=endpoint)


def test_executor_never_follows_redirects_to_another_recipient():
    handler = remote_executor._NoExecutorRedirects()
    assert handler.redirect_request(None, None, 302, "redirect", {}, "https://other.test") is None


@pytest.mark.parametrize("result,code", [
    ({"status": "completed", "artifactIds": "artifact:not-an-array"}, "RESULT_ARTIFACT_REQUIRED"),
    ({"status": "completed", "artifactIds": [""]}, "RESULT_ARTIFACT_REQUIRED"),
    ({"status": "completed", "artifactIds": [123]}, "RESULT_ARTIFACT_REQUIRED"),
    (["completed"], "INVALID_RESPONSE"),
    ({"status": "failed", "error": "upstream-secret"}, "CODING_EXECUTOR_FAILED"),
])
def test_executor_rejects_invalid_results_and_never_repeats_remote_error(monkeypatch, result, code):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self): return json.dumps(result).encode()
    monkeypatch.setattr(remote_executor, "_open_executor_request", lambda *_, **__: Response())
    executor = remote_executor.RemoteIsolatedCodingExecutor(endpoint="http://localhost:9000")
    with pytest.raises(remote_executor.RemoteCodingExecutorError, match=code) as error:
        executor.execute(_job(), heartbeat=lambda: None)
    assert "upstream-secret" not in str(error.value)


def test_executor_network_failure_hides_request_and_token(monkeypatch):
    def fail(*_, **__): raise URLError("Bearer token-must-not-be-stored")
    monkeypatch.setattr(remote_executor, "_open_executor_request", fail)
    executor = remote_executor.RemoteIsolatedCodingExecutor(endpoint="http://localhost:9000")
    with pytest.raises(remote_executor.RemoteCodingExecutorError) as error:
        executor.execute(_job(), heartbeat=lambda: None)
    assert str(error.value) == "CODING_EXECUTOR_CALL_FAILED"
    assert error.value.__cause__ is None


@pytest.mark.parametrize("environment", ["production", "prod", "staging"])
def test_hardened_coding_worker_fails_before_executor_or_repository_setup(monkeypatch, environment):
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", environment)
    monkeypatch.setenv("REALYTICS_CODING_EXECUTOR_URL", "http://unused.invalid")
    monkeypatch.setattr(sys, "argv", ["run_coding_pod_worker.py", "--once"])
    with pytest.raises(SystemExit, match="Coding Pod worker is disabled"):
        run_coding_pod_worker.main()


def test_coding_worker_documented_direct_invocation_supports_help():
    api_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["REALYTICS_LOAD_LOCAL_ENV"] = "false"
    completed = subprocess.run([sys.executable, str(api_root / "scripts/run_coding_pod_worker.py"), "--help"],
        cwd=api_root, env=env, capture_output=True, text=True, timeout=10, check=False)
    assert completed.returncode == 0, completed.stderr
    assert "--once" in completed.stdout
