"""Fail-closed connection boundary for Legal Provider operations."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from db import postgres


LEGAL_OPERATIONS_DATABASE_URL_ENV = (
    "REALYTICS_LEGAL_OPERATIONS_DATABASE_URL"
)


class LegalOperationsDatabaseConfigurationError(RuntimeError):
    """The dedicated provider-operations principal is not configured."""


def legal_operations_database_configured(
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    env = os.environ if environ is None else environ
    return bool(
        str(
            env.get(LEGAL_OPERATIONS_DATABASE_URL_ENV) or ""
        ).strip()
    )


def connect_legal_operations_database() -> Any:
    """Connect only through the dedicated append-only operations DSN."""

    dsn = str(
        os.environ.get(LEGAL_OPERATIONS_DATABASE_URL_ENV) or ""
    ).strip()
    if not dsn:
        raise LegalOperationsDatabaseConfigurationError(
            f"{LEGAL_OPERATIONS_DATABASE_URL_ENV} is required for legal "
            "provider DLQ review and replay"
        )
    return postgres.connect(dsn)


__all__ = [
    "LEGAL_OPERATIONS_DATABASE_URL_ENV",
    "LegalOperationsDatabaseConfigurationError",
    "connect_legal_operations_database",
    "legal_operations_database_configured",
]
