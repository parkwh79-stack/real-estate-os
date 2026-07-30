"""Run the durable PF review worker.

The API process only enqueues jobs in production. This worker claims persisted
``async_jobs`` records with a database lease and executes them through the
durable Agent Runtime.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers.projects import orchestration_repository  # noqa: E402
from use_cases.async_jobs import PFReviewJobWorker  # noqa: E402


def repository_for_job(job: dict):
    """Bind every worker repository to the tenant persisted on its job."""

    tenant_id = str(job.get("tenantId") or "").strip()
    if not tenant_id:
        raise RuntimeError("A persisted tenantId is required for a PF review job")
    return orchestration_repository(tenant_id=tenant_id)


def main() -> None:
    poll_seconds = float(os.environ.get("REALYTICS_ASYNC_JOB_POLL_SECONDS", "1"))
    worker = PFReviewJobWorker(repository_for_job)
    worker.run_forever(poll_seconds=poll_seconds)


if __name__ == "__main__":
    main()
