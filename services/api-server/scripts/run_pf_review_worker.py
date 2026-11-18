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

from use_cases.async_jobs import PFReviewJobWorker  # noqa: E402


def repository_for_job(job: dict):
    """Bind every worker repository to the tenant persisted on its job."""

    tenant_id = str(job.get("tenantId") or "").strip()
    if not tenant_id:
        raise RuntimeError("A persisted tenantId is required for a PF review job")
    from storage import storage_backend
    if storage_backend() != "postgres":
        from routers.projects import orchestration_repository
        return orchestration_repository(tenant_id=tenant_id)
    from db.postgres_runtime_pool import pf_worker_tenant_transaction
    from db.repositories.orchestration_repository import PostgresOrchestrationRepository
    from db.repositories.report_repository import PostgresReportRepository
    from db.repositories.project_snapshot_repository import PostgresProjectSnapshotRepository
    kwargs = {"tenant_id": tenant_id, "transaction_factory": pf_worker_tenant_transaction}
    return PostgresOrchestrationRepository(**kwargs,
        report_repository=PostgresReportRepository(**kwargs),
        project_snapshot_repository=PostgresProjectSnapshotRepository(**kwargs))


def main() -> None:
    poll_seconds = float(os.environ.get("REALYTICS_ASYNC_JOB_POLL_SECONDS", "1"))
    tenant_id = os.environ.get("REALYTICS_PF_WORKER_TENANT_ID", "").strip()
    worker = PFReviewJobWorker(repository_for_job, tenant_id=tenant_id)
    from storage import storage_backend
    if storage_backend() != "postgres":
        worker.run_forever(poll_seconds=poll_seconds)
        return
    from db.postgres_runtime_pool import RUNTIME_POOL_ROLES_ENV, open_runtime_pools, close_runtime_pools
    if not tenant_id or not os.environ.get("REALYTICS_PF_WORKER_DATABASE_URL"):
        raise RuntimeError("The PF worker requires an explicit tenant and dedicated database URL")
    if os.environ.get(RUNTIME_POOL_ROLES_ENV, "pf_worker").strip() != "pf_worker":
        raise RuntimeError("The PF worker must open only the pf_worker pool")
    os.environ.setdefault(RUNTIME_POOL_ROLES_ENV, "pf_worker")
    try:
        open_runtime_pools()
        worker.run_forever(poll_seconds=poll_seconds)
    finally:
        close_runtime_pools()


if __name__ == "__main__":
    main()
