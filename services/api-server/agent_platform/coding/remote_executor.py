from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from agent_platform.contracts import CodingPodJob


class RemoteCodingExecutorError(RuntimeError):
    pass


class RemoteIsolatedCodingExecutor:
    """Calls a separately deployed sandbox executor; never runs code in the API process."""

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        token: str | None = None,
        timeout_seconds: int = 900,
        heartbeat_seconds: int = 20,
    ) -> None:
        self.endpoint = (endpoint or os.environ.get("REALYTICS_CODING_EXECUTOR_URL") or "").rstrip("/")
        self.token = token or os.environ.get("REALYTICS_CODING_EXECUTOR_TOKEN")
        self.timeout_seconds = max(30, int(timeout_seconds))
        self.heartbeat_seconds = max(5, int(heartbeat_seconds))
        self._validate_endpoint()

    def execute(
        self,
        job: CodingPodJob,
        *,
        heartbeat: Callable[[], None],
    ) -> list[str]:
        stop = threading.Event()
        heartbeat_error: list[Exception] = []

        def keep_lease() -> None:
            while not stop.wait(self.heartbeat_seconds):
                try:
                    heartbeat()
                except Exception as exc:  # worker result must not outlive a lost lease
                    heartbeat_error.append(exc)
                    stop.set()

        lease_thread = threading.Thread(
            target=keep_lease,
            name=f"coding-heartbeat-{job.job_id}",
            daemon=True,
        )
        heartbeat()
        lease_thread.start()
        try:
            payload = {
                "schemaVersion": "realytics-isolated-coding-execution-request-v1",
                "jobId": job.job_id,
                "tenantId": job.tenant_id,
                "workspaceRef": job.workspace_ref,
                "branchName": job.branch_name,
                "requestedChange": job.requested_change,
                "allowedPaths": list(job.allowed_paths),
                "attemptCount": job.attempt_count,
                "networkPolicy": "executor_defined_deny_by_default",
                "credentialsIncluded": False,
            }
            request = urllib.request.Request(
                f"{self.endpoint}/v1/coding-jobs/execute",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    result = json.loads(response.read().decode("utf-8"))
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                raise RemoteCodingExecutorError(f"CODING_EXECUTOR_CALL_FAILED: {exc}") from exc
            if heartbeat_error:
                raise RemoteCodingExecutorError(
                    f"CODING_EXECUTOR_LEASE_LOST: {heartbeat_error[0]}"
                )
            if str(result.get("status") or "").lower() not in {"completed", "succeeded"}:
                raise RemoteCodingExecutorError(
                    str(result.get("error") or result.get("message") or "CODING_EXECUTOR_FAILED")
                )
            artifact_ids = [str(item) for item in result.get("artifactIds") or [] if item]
            if not artifact_ids:
                raise RemoteCodingExecutorError("CODING_EXECUTOR_RESULT_ARTIFACT_REQUIRED")
            return artifact_ids
        finally:
            stop.set()
            lease_thread.join(timeout=2)

    def _validate_endpoint(self) -> None:
        if not self.endpoint:
            raise RemoteCodingExecutorError("REALYTICS_CODING_EXECUTOR_URL_REQUIRED")
        parsed = urllib.parse.urlparse(self.endpoint)
        localhost = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme not in ({"http", "https"} if localhost else {"https"}):
            raise RemoteCodingExecutorError(
                "CODING_EXECUTOR_HTTPS_REQUIRED_OUTSIDE_LOCALHOST"
            )
