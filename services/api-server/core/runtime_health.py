"""Secret-safe process liveness and deployment readiness projections."""

from __future__ import annotations

import os
from typing import Any

from core.runtime_profile import (
    deployment_environment,
    identity_membership_authority,
    is_hardened_environment,
)
from db.malware_scanner import get_malware_scanner
from db.object_storage import get_object_storage
from db.postgres_runtime_pool import RuntimePoolName
from storage import storage_backend


def liveness_payload() -> dict[str, Any]:
    """Report only that the Python process can answer requests."""

    return {
        "schemaVersion": "realytics-runtime-health-v1",
        "status": "alive",
        "checks": {"process": "ok"},
    }


def readiness_payload(application: Any) -> tuple[int, dict[str, Any]]:
    """Check runtime authority wiring without returning credentials or hosts.

    Hardened API containers require live API and identity pool checkouts. S3
    and ClamAV are configuration checks here; their live dependency drills are
    separate operational Gates and are labelled as such in the response.
    """

    hardened = is_hardened_environment()
    backend = storage_backend()
    registry = getattr(application.state, "postgres_runtime_pools", None)
    failures: list[str] = []
    pool_checks: dict[str, str] = {}

    required_pools: tuple[RuntimePoolName, ...]
    if hardened:
        required_pools = (RuntimePoolName.API, RuntimePoolName.IDENTITY)
    elif backend in {"postgres", "postgresql"} and registry is not None:
        required_pools = tuple(registry.specs)
    else:
        required_pools = ()

    if (hardened or backend in {"postgres", "postgresql"}) and (
        registry is None or not registry.configured or not registry.opened
    ):
        failures.append("postgres_runtime_pools_unavailable")
    elif registry is not None:
        for pool_name in required_pools:
            try:
                pool, spec = registry.require_pool(pool_name)
                with pool.connection(
                    timeout=spec.acquire_timeout_seconds
                ) as connection:
                    with connection.transaction():
                        cursor = connection.execute("SELECT 1")
                        if cursor.fetchone() != (1,):
                            raise RuntimeError("readiness_query_failed")
                pool_checks[pool_name.value] = "ok"
            except Exception:
                # Never include the driver exception: connection errors can
                # contain a DSN, host, user, or other deployment secret.
                pool_checks[pool_name.value] = "unavailable"
                failures.append(f"postgres_{pool_name.value}_unavailable")

    object_storage_status = "development_local"
    malware_status = "development_bypass"
    if hardened:
        try:
            object_storage = get_object_storage()
            if object_storage.backend not in {"s3", "gcs"}:
                raise RuntimeError("managed_object_storage_required")
            object_storage_status = "configured"
        except Exception:
            object_storage_status = "invalid"
            failures.append("object_storage_configuration_invalid")
        try:
            scanner = get_malware_scanner()
            if scanner.__class__.__name__ != "ClamAVTcpScanner":
                raise RuntimeError("clamav_required")
            malware_status = "configured"
        except Exception:
            malware_status = "invalid"
            failures.append("malware_scanner_configuration_invalid")

    ready = not failures
    return (
        200 if ready else 503,
        {
            "schemaVersion": "realytics-runtime-readiness-v1",
            "status": "ready" if ready else "not_ready",
            "environment": deployment_environment(),
            "authority": {
                "businessData": (
                    "postgres" if backend in {"postgres", "postgresql"} else backend
                ),
                "membership": identity_membership_authority(),
                "objectBytes": (
                    (os.environ.get("REALYTICS_OBJECT_STORAGE") or "").strip().casefold()
                    if (os.environ.get("REALYTICS_OBJECT_STORAGE") or "").strip().casefold()
                    in {"s3", "gcs"}
                    else "local"
                ),
            },
            "checks": {
                "postgresPools": pool_checks,
                "objectStorage": {
                    "configuration": object_storage_status,
                    "connectivityChecked": False,
                },
                "malwareScanner": {
                    "configuration": malware_status,
                    "connectivityChecked": False,
                },
            },
            "failureCodes": sorted(set(failures)),
        },
    )


__all__ = ["liveness_payload", "readiness_payload"]
