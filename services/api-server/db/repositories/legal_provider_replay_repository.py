"""Append-only PostgreSQL repository for two-person legal-provider replay.

Raw object identity enters only through a trusted worker's immutable catalog
receipt. A separately authenticated human approver references its server-issued
id, and replay callers submit only the resulting approval event id. This
repository resolves and revalidates the exact object version/digest/retention
binding under advisory locks before consuming it once. Dead-letter metadata is
intentionally global and privacy-safe, while every object/approval/request/event
projection is evaluated under an explicit tenant session and forced PostgreSQL
RLS.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from db.legal_operations_database import connect_legal_operations_database
from db.postgres_runtime_privileges import validate_database_identifier
from legal_intelligence.provider_replay_models import (
    LegalProviderReplayObjectReceipt,
    ProviderReplayStatus,
)


ConnectionFactory = Callable[[], Any]


class ReplayRecordNotFound(LookupError):
    pass


class ReplayContractConflict(ValueError):
    pass


class ReplayStateConflict(ValueError):
    pass


class ReplayApprovalExpired(ReplayContractConflict):
    pass


class ReplayApprovalConsumed(ReplayContractConflict):
    pass


@dataclass(frozen=True)
class ProviderReplayMaterial:
    """Internal-only material handed to the injected replay executor."""

    replay_request_id: str
    dead_letter_id: str
    tenant_id: str
    project_id: str
    provider_id: str
    operation: str
    target: str | None
    replay_object_id: str
    approved_object_locator: str
    approved_object_version_id: str
    approved_object_sha256: str
    object_retention_until: datetime


class PostgresLegalProviderReplayRepository:
    """Persist approval, request, and lifecycle streams without mutation."""

    def __init__(
        self,
        *,
        connection_factory: ConnectionFactory = (
            connect_legal_operations_database
        ),
        schema_name: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        id_factory: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        self._connect = connection_factory
        self._schema = validate_database_identifier(
            schema_name
            or os.environ.get("REALYTICS_DATABASE_SCHEMA")
            or "public",
            label="database schema",
        )
        self._clock = clock
        self._id_factory = id_factory

    def register_replay_object(
        self,
        receipt: LegalProviderReplayObjectReceipt,
    ) -> dict[str, Any]:
        """Append a worker-attested immutable object-store receipt.

        No HTTP route calls this method.  The trusted object worker supplies an
        exact object version and retention receipt through the dedicated
        legal-operations database principal before a human can approve replay.
        """

        replay_object_id = (
            f"legal-provider-replay-object:{self._id_factory()}"
        )
        registered_at = _aware(self._clock())
        retention_until = _aware(receipt.retention_until)
        locator_sha256 = _locator_sha256(receipt.object_locator)
        if retention_until <= registered_at:
            raise ReplayContractConflict(
                "replay object retention must be in the future"
            )
        if not _locator_binds_dead_letter(
            receipt.object_locator,
            receipt.dead_letter_id,
        ):
            raise ReplayContractConflict(
                "replay object locator is not exactly bound to the "
                "dead-letter identifier"
            )

        with self._connect() as connection:
            with connection.cursor() as cursor:
                self._set_search_path(cursor)
                self._set_tenant(cursor, receipt.tenant_id)
                self._lock_aggregate(
                    cursor,
                    f"dead-letter:{receipt.dead_letter_id}",
                )
                self._lock_aggregate(
                    cursor,
                    "replay-object:"
                    f"{receipt.provider_id}:{locator_sha256}:"
                    f"{receipt.object_version_id}",
                )
                cursor.execute(
                    """
                    SELECT provider_id
                    FROM legal_provider_dead_letters
                    WHERE legal_provider_dead_letter_id = %s
                    """,
                    (receipt.dead_letter_id,),
                )
                dead_letter = cursor.fetchone()
                if dead_letter is None:
                    raise ReplayRecordNotFound(receipt.dead_letter_id)
                if str(dead_letter[0]) != receipt.provider_id:
                    raise ReplayContractConflict(
                        "replay object provider does not match the dead-letter "
                        "provider"
                    )

                cursor.execute(
                    """
                    SELECT
                        legal_provider_replay_object_id,
                        project_id,
                        provider_id,
                        object_locator_sha256,
                        object_version_id,
                        object_sha256,
                        object_lock_mode,
                        retention_until,
                        object_store_receipt_sha256,
                        registered_by,
                        registered_at
                    FROM legal_provider_replay_objects
                    WHERE tenant_id = %s
                      AND legal_provider_dead_letter_id = %s
                      AND idempotency_key = %s
                    """,
                    (
                        receipt.tenant_id,
                        receipt.dead_letter_id,
                        receipt.idempotency_key,
                    ),
                )
                existing = cursor.fetchone()
                immutable = (
                    receipt.project_id,
                    receipt.provider_id,
                    locator_sha256,
                    receipt.object_version_id,
                    receipt.object_sha256,
                    receipt.object_lock_mode.value,
                    retention_until,
                    receipt.object_store_receipt_sha256,
                    receipt.registered_by,
                )
                if existing is not None:
                    if tuple(existing[1:10]) != immutable:
                        raise ReplayContractConflict(
                            "replay object receipt idempotency key was reused "
                            "with a different immutable binding"
                        )
                    return _replay_object_projection(
                        replay_object_id=existing[0],
                        dead_letter_id=receipt.dead_letter_id,
                        project_id=receipt.project_id,
                        provider_id=receipt.provider_id,
                        object_version_id=receipt.object_version_id,
                        retention_until=retention_until,
                        registered_at=existing[10],
                    )

                cursor.execute(
                    """
                    INSERT INTO legal_provider_replay_objects (
                        legal_provider_replay_object_id,
                        tenant_id,
                        project_id,
                        legal_provider_dead_letter_id,
                        provider_id,
                        object_locator,
                        object_locator_sha256,
                        object_version_id,
                        object_sha256,
                        object_lock_mode,
                        retention_until,
                        object_store_receipt_sha256,
                        registered_by,
                        registered_at,
                        idempotency_key
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        replay_object_id,
                        receipt.tenant_id,
                        receipt.project_id,
                        receipt.dead_letter_id,
                        receipt.provider_id,
                        receipt.object_locator,
                        locator_sha256,
                        receipt.object_version_id,
                        receipt.object_sha256,
                        receipt.object_lock_mode.value,
                        retention_until,
                        receipt.object_store_receipt_sha256,
                        receipt.registered_by,
                        registered_at,
                        receipt.idempotency_key,
                    ),
                )
                return _replay_object_projection(
                    replay_object_id=replay_object_id,
                    dead_letter_id=receipt.dead_letter_id,
                    project_id=receipt.project_id,
                    provider_id=receipt.provider_id,
                    object_version_id=receipt.object_version_id,
                    retention_until=retention_until,
                    registered_at=registered_at,
                )

    def list_dead_letters(
        self,
        *,
        tenant_id: str,
        limit: int = 100,
        provider_id: str | None = None,
        status: ProviderReplayStatus | None = None,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 500))
        normalized_provider = str(provider_id or "").strip() or None
        normalized_status = status.value if status is not None else None
        with self._connect() as connection:
            with connection.cursor() as cursor:
                self._set_search_path(cursor)
                self._set_tenant(cursor, tenant_id)
                cursor.execute(
                    """
                    SELECT
                        d.legal_provider_dead_letter_id,
                        d.provider_id,
                        d.operation,
                        d.reason_code,
                        d.attempts,
                        d.target,
                        d.occurred_at,
                        request_state.legal_provider_replay_request_id,
                        COALESCE(event_state.event_type, 'not_requested'),
                        request_state.requested_at,
                        event_state.recorded_at,
                        (request_state.legal_provider_replay_object_id IS NOT NULL),
                        EXISTS (
                            SELECT 1
                            FROM legal_provider_replay_objects AS replay_object
                            WHERE replay_object.legal_provider_dead_letter_id =
                                d.legal_provider_dead_letter_id
                              AND replay_object.tenant_id = %s
                              AND replay_object.retention_until >
                                  statement_timestamp()
                        )
                    FROM legal_provider_dead_letters AS d
                    LEFT JOIN LATERAL (
                        SELECT
                            r.legal_provider_replay_request_id,
                            r.legal_provider_replay_object_id,
                            r.requested_at
                        FROM legal_provider_replay_requests AS r
                        WHERE r.legal_provider_dead_letter_id =
                            d.legal_provider_dead_letter_id
                          AND r.tenant_id = %s
                        ORDER BY
                            r.requested_at DESC,
                            r.legal_provider_replay_request_id DESC
                        LIMIT 1
                    ) AS request_state ON TRUE
                    LEFT JOIN LATERAL (
                        SELECT e.event_type, e.recorded_at
                        FROM legal_provider_replay_events AS e
                        WHERE e.legal_provider_replay_request_id =
                            request_state.legal_provider_replay_request_id
                        ORDER BY
                            e.sequence_no DESC,
                            e.legal_provider_replay_event_id DESC
                        LIMIT 1
                    ) AS event_state ON TRUE
                    WHERE (%s IS NULL OR d.provider_id = %s)
                      AND (
                          %s IS NULL
                          OR COALESCE(event_state.event_type, 'not_requested') = %s
                      )
                    ORDER BY
                        d.occurred_at DESC,
                        d.legal_provider_dead_letter_id DESC
                    LIMIT %s
                    """,
                    (
                        tenant_id,
                        tenant_id,
                        normalized_provider,
                        normalized_provider,
                        normalized_status,
                        normalized_status,
                        bounded_limit,
                    ),
                )
                return [
                    _dead_letter_projection(row)
                    for row in cursor.fetchall()
                ]

    def create_replay_approval(
        self,
        dead_letter_id: str,
        *,
        tenant_id: str,
        project_id: str,
        replay_object_id: str,
        replay_requester_id: str,
        approved_by: str,
        approver_role: str,
        approved_at: datetime,
        expires_at: datetime,
        idempotency_key: str,
        supersedes_approval_event_id: str | None,
    ) -> dict[str, Any]:
        approval_event_id = (
            f"legal-provider-replay-approval:{self._id_factory()}"
        )
        approved_at = _aware(approved_at)
        expires_at = _aware(expires_at)
        if (
            expires_at <= approved_at
            or expires_at - approved_at > timedelta(hours=24)
        ):
            raise ReplayContractConflict(
                "approval expiry must be after approval and within 24 hours"
            )
        if approver_role != "compliance_officer":
            raise ReplayContractConflict(
                "approval event requires compliance_officer role"
            )
        if approved_by.casefold() == replay_requester_id.casefold():
            raise ReplayContractConflict(
                "replay requester and object approver must differ"
            )
        with self._connect() as connection:
            with connection.cursor() as cursor:
                self._set_search_path(cursor)
                self._set_tenant(cursor, tenant_id)
                if supersedes_approval_event_id is not None:
                    self._lock_aggregate(
                        cursor,
                        "legal-provider-replay-approval:"
                        f"{supersedes_approval_event_id}",
                    )
                self._lock_aggregate(cursor, f"dead-letter:{dead_letter_id}")
                self._lock_aggregate(
                    cursor,
                    ":".join(
                        (
                            "legal-provider-replay-approval",
                            tenant_id,
                            project_id,
                            dead_letter_id,
                            replay_requester_id.strip().casefold(),
                        )
                    ),
                )
                cursor.execute(
                    """
                    SELECT
                        replay_object.provider_id,
                        replay_object.object_locator,
                        replay_object.object_locator_sha256,
                        replay_object.object_version_id,
                        replay_object.object_sha256,
                        replay_object.retention_until,
                        replay_object.object_store_receipt_sha256,
                        replay_object.registered_at
                    FROM legal_provider_replay_objects AS replay_object
                    JOIN legal_provider_dead_letters AS dead_letter
                      ON dead_letter.legal_provider_dead_letter_id =
                         replay_object.legal_provider_dead_letter_id
                     AND dead_letter.provider_id = replay_object.provider_id
                    WHERE replay_object.legal_provider_replay_object_id = %s
                      AND replay_object.tenant_id = %s
                      AND replay_object.project_id = %s
                      AND replay_object.legal_provider_dead_letter_id = %s
                      AND replay_object.retention_until >= %s
                      AND replay_object.registered_at <= %s
                    """,
                    (
                        replay_object_id,
                        tenant_id,
                        project_id,
                        dead_letter_id,
                        expires_at,
                        approved_at,
                    ),
                )
                catalog = cursor.fetchone()
                if catalog is None:
                    raise ReplayContractConflict(
                        "a current immutable replay object receipt is required"
                    )
                (
                    provider_id,
                    approved_object_locator,
                    locator_sha256,
                    approved_object_version_id,
                    approved_object_sha256,
                    object_retention_until,
                    object_store_receipt_sha256,
                    _object_registered_at,
                ) = catalog

                cursor.execute(
                    """
                    SELECT
                        legal_provider_replay_approval_event_id,
                        project_id,
                        legal_provider_replay_object_id,
                        provider_id,
                        approved_object_locator_sha256,
                        approved_object_version_id,
                        approved_object_sha256,
                        approved_object_retention_until,
                        object_store_receipt_sha256,
                        approved_requester_id,
                        approved_by,
                        approver_role,
                        approved_at,
                        expires_at,
                        supersedes_approval_event_id
                    FROM legal_provider_replay_approval_events
                    WHERE tenant_id = %s
                      AND legal_provider_dead_letter_id = %s
                      AND idempotency_key = %s
                    """,
                    (tenant_id, dead_letter_id, idempotency_key),
                )
                existing = cursor.fetchone()
                immutable = (
                    project_id,
                    replay_object_id,
                    provider_id,
                    locator_sha256,
                    approved_object_version_id,
                    approved_object_sha256,
                    object_retention_until,
                    object_store_receipt_sha256,
                    replay_requester_id,
                    approved_by,
                    approver_role,
                    approved_at,
                    expires_at,
                    supersedes_approval_event_id,
                )
                if existing is not None:
                    if tuple(existing[1:]) != immutable:
                        raise ReplayContractConflict(
                            "approval idempotency key was reused with a "
                            "different immutable approval binding"
                        )
                    return _approval_projection(
                        approval_event_id=existing[0],
                        dead_letter_id=dead_letter_id,
                        project_id=project_id,
                        replay_object_id=replay_object_id,
                        approved_requester_id=replay_requester_id,
                        approved_at=approved_at,
                        expires_at=expires_at,
                        supersedes_approval_event_id=(
                            supersedes_approval_event_id
                        ),
                    )

                if supersedes_approval_event_id is not None:
                    cursor.execute(
                        """
                        SELECT previous.legal_provider_replay_approval_event_id
                        FROM legal_provider_replay_approval_events AS previous
                        WHERE previous.legal_provider_replay_approval_event_id = %s
                          AND previous.tenant_id = %s
                          AND previous.project_id = %s
                          AND previous.legal_provider_dead_letter_id = %s
                          AND lower(btrim(previous.approved_requester_id)) =
                              lower(btrim(%s))
                          AND previous.expires_at > %s
                          AND NOT EXISTS (
                              SELECT 1
                              FROM legal_provider_replay_approval_events AS next
                              WHERE next.supersedes_approval_event_id =
                                  previous.legal_provider_replay_approval_event_id
                          )
                          AND NOT EXISTS (
                              SELECT 1
                              FROM legal_provider_replay_requests AS consumed
                              WHERE consumed.legal_provider_replay_approval_event_id =
                                  previous.legal_provider_replay_approval_event_id
                          )
                        """,
                        (
                            supersedes_approval_event_id,
                            tenant_id,
                            project_id,
                            dead_letter_id,
                            replay_requester_id,
                            approved_at,
                        ),
                    )
                    if cursor.fetchone() is None:
                        raise ReplayContractConflict(
                            "superseded approval must be current, same-scope, "
                            "and unconsumed"
                        )
                else:
                    cursor.execute(
                        """
                        SELECT current.legal_provider_replay_approval_event_id
                        FROM legal_provider_replay_approval_events AS current
                        WHERE current.tenant_id = %s
                          AND current.project_id = %s
                          AND current.legal_provider_dead_letter_id = %s
                          AND current.approved_requester_id = %s
                          AND current.expires_at > %s
                          AND NOT EXISTS (
                              SELECT 1
                              FROM legal_provider_replay_approval_events AS next
                              WHERE next.supersedes_approval_event_id =
                                  current.legal_provider_replay_approval_event_id
                          )
                          AND NOT EXISTS (
                              SELECT 1
                              FROM legal_provider_replay_requests AS consumed
                              WHERE consumed.legal_provider_replay_approval_event_id =
                                  current.legal_provider_replay_approval_event_id
                          )
                        LIMIT 1
                        """,
                        (
                            tenant_id,
                            project_id,
                            dead_letter_id,
                            replay_requester_id,
                            approved_at,
                        ),
                    )
                    if cursor.fetchone() is not None:
                        raise ReplayContractConflict(
                            "a current unconsumed approval already exists; "
                            "an explicit supersedesApprovalEventId is required"
                        )

                cursor.execute(
                    """
                    INSERT INTO legal_provider_replay_approval_events (
                        legal_provider_replay_approval_event_id,
                        tenant_id,
                        project_id,
                        legal_provider_dead_letter_id,
                        legal_provider_replay_object_id,
                        provider_id,
                        approved_object_locator,
                        approved_object_locator_sha256,
                        approved_object_version_id,
                        approved_object_sha256,
                        approved_object_retention_until,
                        object_store_receipt_sha256,
                        approved_requester_id,
                        approved_by,
                        approver_role,
                        approved_at,
                        expires_at,
                        idempotency_key,
                        supersedes_approval_event_id
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s
                    )
                    """,
                    (
                        approval_event_id,
                        tenant_id,
                        project_id,
                        dead_letter_id,
                        replay_object_id,
                        provider_id,
                        approved_object_locator,
                        locator_sha256,
                        approved_object_version_id,
                        approved_object_sha256,
                        object_retention_until,
                        object_store_receipt_sha256,
                        replay_requester_id,
                        approved_by,
                        approver_role,
                        approved_at,
                        expires_at,
                        idempotency_key,
                        supersedes_approval_event_id,
                    ),
                )
                return _approval_projection(
                    approval_event_id=approval_event_id,
                    dead_letter_id=dead_letter_id,
                    project_id=project_id,
                    replay_object_id=replay_object_id,
                    approved_requester_id=replay_requester_id,
                    approved_at=approved_at,
                    expires_at=expires_at,
                    supersedes_approval_event_id=(
                        supersedes_approval_event_id
                    ),
                )

    def request_replay(
        self,
        dead_letter_id: str,
        *,
        tenant_id: str,
        approval_event_id: str,
        idempotency_key: str,
        requested_by: str,
        requester_role: str,
    ) -> dict[str, Any]:
        request_id = f"legal-provider-replay:{self._id_factory()}"
        event_id = f"legal-provider-replay-event:{self._id_factory()}"
        occurred_at = _aware(self._clock())
        with self._connect() as connection:
            with connection.cursor() as cursor:
                self._set_search_path(cursor)
                self._set_tenant(cursor, tenant_id)
                self._lock_aggregate(
                    cursor,
                    f"legal-provider-replay-approval:{approval_event_id}",
                )
                self._lock_aggregate(cursor, f"dead-letter:{dead_letter_id}")

                cursor.execute(
                    """
                    SELECT
                        request.legal_provider_replay_request_id,
                        request.legal_provider_replay_approval_event_id,
                        request.requested_by
                    FROM legal_provider_replay_requests AS request
                    JOIN legal_provider_replay_approval_events AS approval
                      ON approval.legal_provider_replay_approval_event_id =
                         request.legal_provider_replay_approval_event_id
                    WHERE request.legal_provider_dead_letter_id = %s
                      AND request.idempotency_key = %s
                      AND approval.tenant_id = %s
                    """,
                    (dead_letter_id, idempotency_key, tenant_id),
                )
                replay = cursor.fetchone()
                if replay is not None:
                    if (
                        replay[1] != approval_event_id
                        or str(replay[2]).casefold()
                        != requested_by.casefold()
                    ):
                        raise ReplayContractConflict(
                            "replay idempotency key was reused with a "
                            "different approval event"
                        )
                    return self._request_status(cursor, replay[0])

                cursor.execute(
                    """
                    SELECT
                        approval.project_id,
                        approval.legal_provider_replay_object_id,
                        approval.provider_id,
                        approval.approved_object_locator,
                        approval.approved_object_locator_sha256,
                        approval.approved_object_version_id,
                        approval.approved_object_sha256,
                        approval.approved_object_retention_until,
                        approval.object_store_receipt_sha256,
                        approval.approved_requester_id,
                        approval.approved_by,
                        approval.approved_at,
                        approval.expires_at,
                        successor.legal_provider_replay_approval_event_id,
                        consumed.legal_provider_replay_request_id
                    FROM legal_provider_replay_approval_events AS approval
                    LEFT JOIN legal_provider_replay_approval_events AS successor
                      ON successor.supersedes_approval_event_id =
                         approval.legal_provider_replay_approval_event_id
                    LEFT JOIN legal_provider_replay_requests AS consumed
                      ON consumed.legal_provider_replay_approval_event_id =
                         approval.legal_provider_replay_approval_event_id
                    WHERE approval.legal_provider_replay_approval_event_id = %s
                      AND approval.legal_provider_dead_letter_id = %s
                      AND approval.tenant_id = %s
                    """,
                    (approval_event_id, dead_letter_id, tenant_id),
                )
                approval = cursor.fetchone()
                if approval is None:
                    raise ReplayRecordNotFound(approval_event_id)
                (
                    project_id,
                    replay_object_id,
                    provider_id,
                    approved_object_locator,
                    locator_sha256,
                    approved_object_version_id,
                    approved_object_sha256,
                    object_retention_until,
                    object_store_receipt_sha256,
                    approved_requester_id,
                    approved_by,
                    approved_at,
                    expires_at,
                    successor_id,
                    consumed_request_id,
                ) = approval
                if successor_id is not None:
                    raise ReplayContractConflict(
                        "replay approval has been superseded"
                    )
                if consumed_request_id is not None:
                    raise ReplayApprovalConsumed(
                        "replay approval was already consumed"
                    )
                if _aware(expires_at) <= occurred_at:
                    raise ReplayApprovalExpired(
                        "replay approval has expired"
                    )
                if _aware(object_retention_until) <= occurred_at:
                    raise ReplayApprovalExpired(
                        "registered replay object retention has expired"
                    )
                if (
                    str(approved_requester_id).casefold()
                    != requested_by.casefold()
                ):
                    raise ReplayContractConflict(
                        "authenticated replay requester does not match the "
                        "approved requester"
                    )
                if str(approved_by).casefold() == requested_by.casefold():
                    raise ReplayContractConflict(
                        "replay requester and object approver must differ"
                    )
                if (
                    _locator_sha256(approved_object_locator)
                    != locator_sha256
                    or not _locator_binds_dead_letter(
                        approved_object_locator,
                        dead_letter_id,
                    )
                ):
                    raise ReplayContractConflict(
                        "approval event object locator binding is invalid"
                    )

                cursor.execute(
                    """
                    INSERT INTO legal_provider_replay_requests (
                        legal_provider_replay_request_id,
                        legal_provider_replay_approval_event_id,
                        tenant_id,
                        project_id,
                        legal_provider_dead_letter_id,
                        legal_provider_replay_object_id,
                        provider_id,
                        approved_object_locator,
                        approved_object_version_id,
                        approved_object_sha256,
                        approved_object_retention_until,
                        object_store_receipt_sha256,
                        object_reference_approved_by,
                        object_reference_approved_at,
                        idempotency_key,
                        requested_by,
                        requester_role,
                        requested_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s
                    )
                    """,
                    (
                        request_id,
                        approval_event_id,
                        tenant_id,
                        project_id,
                        dead_letter_id,
                        replay_object_id,
                        provider_id,
                        approved_object_locator,
                        approved_object_version_id,
                        approved_object_sha256,
                        object_retention_until,
                        object_store_receipt_sha256,
                        approved_by,
                        approved_at,
                        idempotency_key,
                        requested_by,
                        requester_role,
                        occurred_at,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO legal_provider_replay_events (
                        legal_provider_replay_event_id,
                        legal_provider_replay_request_id,
                        sequence_no,
                        event_type,
                        recorded_by,
                        recorded_at
                    ) VALUES (%s, %s, 1, 'requested', %s, %s)
                    """,
                    (event_id, request_id, requested_by, occurred_at),
                )
                return _request_projection(
                    request_id=request_id,
                    dead_letter_id=dead_letter_id,
                    status=ProviderReplayStatus.REQUESTED,
                    requested_at=occurred_at,
                    updated_at=occurred_at,
                    object_reference_present=True,
                )

    def begin_replay(
        self,
        replay_request_id: str,
        *,
        worker_id: str,
        tenant_id: str,
    ) -> ProviderReplayMaterial:
        event_id = f"legal-provider-replay-event:{self._id_factory()}"
        occurred_at = _aware(self._clock())
        with self._connect() as connection:
            with connection.cursor() as cursor:
                self._set_search_path(cursor)
                self._set_tenant(cursor, tenant_id)
                self._lock_aggregate(cursor, f"replay:{replay_request_id}")
                cursor.execute(
                    """
                    SELECT
                        request.legal_provider_replay_request_id,
                        request.legal_provider_dead_letter_id,
                        request.tenant_id,
                        request.project_id,
                        dead_letter.provider_id,
                        dead_letter.operation,
                        dead_letter.target,
                        request.legal_provider_replay_object_id,
                        request.approved_object_locator,
                        request.approved_object_version_id,
                        request.approved_object_sha256,
                        request.approved_object_retention_until,
                        request.object_store_receipt_sha256,
                        approval.legal_provider_replay_object_id,
                        approval.provider_id,
                        approval.approved_object_locator,
                        approval.approved_object_locator_sha256,
                        approval.approved_object_version_id,
                        approval.approved_object_sha256,
                        approval.approved_object_retention_until,
                        approval.object_store_receipt_sha256,
                        replay_object.provider_id,
                        replay_object.object_locator,
                        replay_object.object_locator_sha256,
                        replay_object.object_version_id,
                        replay_object.object_sha256,
                        replay_object.retention_until,
                        replay_object.object_store_receipt_sha256
                    FROM legal_provider_replay_requests AS request
                    JOIN legal_provider_dead_letters AS dead_letter
                      ON dead_letter.legal_provider_dead_letter_id =
                         request.legal_provider_dead_letter_id
                    JOIN legal_provider_replay_approval_events AS approval
                      ON approval.legal_provider_replay_approval_event_id =
                         request.legal_provider_replay_approval_event_id
                     AND approval.legal_provider_dead_letter_id =
                         request.legal_provider_dead_letter_id
                     AND approval.tenant_id = request.tenant_id
                     AND approval.project_id = request.project_id
                    JOIN legal_provider_replay_objects AS replay_object
                      ON replay_object.legal_provider_replay_object_id =
                         request.legal_provider_replay_object_id
                     AND replay_object.tenant_id = request.tenant_id
                     AND replay_object.project_id = request.project_id
                     AND replay_object.legal_provider_dead_letter_id =
                         request.legal_provider_dead_letter_id
                    WHERE request.legal_provider_replay_request_id = %s
                      AND request.tenant_id = %s
                    """,
                    (replay_request_id, tenant_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ReplayRecordNotFound(replay_request_id)
                if (
                    not _locator_binds_dead_letter(row[8], row[1])
                    or _locator_sha256(row[8]) != row[16]
                    or row[7] != row[13]
                    or row[4] != row[14]
                    or row[8] != row[15]
                    or row[9] != row[17]
                    or row[10] != row[18]
                    or row[11] != row[19]
                    or row[12] != row[20]
                    or row[4] != row[21]
                    or row[8] != row[22]
                    or row[16] != row[23]
                    or row[9] != row[24]
                    or row[10] != row[25]
                    or row[11] != row[26]
                    or row[12] != row[27]
                    or _aware(row[11]) <= occurred_at
                ):
                    raise ReplayContractConflict(
                        "stored replay request is not exactly bound to a "
                        "current immutable catalog object receipt"
                    )
                sequence_no, state = self._latest_event(
                    cursor,
                    replay_request_id,
                )
                if state != ProviderReplayStatus.REQUESTED.value:
                    raise ReplayStateConflict(
                        f"replay request is {state}, not requested"
                    )
                cursor.execute(
                    """
                    INSERT INTO legal_provider_replay_events (
                        legal_provider_replay_event_id,
                        legal_provider_replay_request_id,
                        sequence_no,
                        event_type,
                        recorded_by,
                        recorded_at
                    ) VALUES (%s, %s, %s, 'running', %s, %s)
                    """,
                    (
                        event_id,
                        replay_request_id,
                        sequence_no + 1,
                        worker_id,
                        occurred_at,
                    ),
                )
                return ProviderReplayMaterial(*row[:12])

    def record_outcome(
        self,
        replay_request_id: str,
        *,
        completed: bool,
        outcome_code: str,
        executor_reference: str | None,
        worker_id: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        event_id = f"legal-provider-replay-event:{self._id_factory()}"
        occurred_at = _aware(self._clock())
        event_type = (
            ProviderReplayStatus.COMPLETED.value
            if completed
            else ProviderReplayStatus.FAILED.value
        )
        with self._connect() as connection:
            with connection.cursor() as cursor:
                self._set_search_path(cursor)
                self._set_tenant(cursor, tenant_id)
                self._lock_aggregate(cursor, f"replay:{replay_request_id}")
                cursor.execute(
                    """
                    SELECT legal_provider_replay_request_id
                    FROM legal_provider_replay_requests
                    WHERE legal_provider_replay_request_id = %s
                      AND tenant_id = %s
                    """,
                    (replay_request_id, tenant_id),
                )
                if cursor.fetchone() is None:
                    raise ReplayRecordNotFound(replay_request_id)
                sequence_no, state = self._latest_event(
                    cursor,
                    replay_request_id,
                )
                if state in {
                    ProviderReplayStatus.COMPLETED.value,
                    ProviderReplayStatus.FAILED.value,
                }:
                    cursor.execute(
                        """
                        SELECT outcome_code, executor_reference
                        FROM legal_provider_replay_events
                        WHERE legal_provider_replay_request_id = %s
                          AND sequence_no = %s
                        """,
                        (replay_request_id, sequence_no),
                    )
                    existing = cursor.fetchone()
                    if (
                        state == event_type
                        and existing == (outcome_code, executor_reference)
                    ):
                        return self._request_status(
                            cursor,
                            replay_request_id,
                        )
                    raise ReplayStateConflict(
                        "replay request already has a different terminal event"
                    )
                if state != ProviderReplayStatus.RUNNING.value:
                    raise ReplayStateConflict(
                        f"replay request is {state}, not running"
                    )
                cursor.execute(
                    """
                    INSERT INTO legal_provider_replay_events (
                        legal_provider_replay_event_id,
                        legal_provider_replay_request_id,
                        sequence_no,
                        event_type,
                        outcome_code,
                        executor_reference,
                        recorded_by,
                        recorded_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        event_id,
                        replay_request_id,
                        sequence_no + 1,
                        event_type,
                        outcome_code,
                        executor_reference,
                        worker_id,
                        occurred_at,
                    ),
                )
                return self._request_status(
                    cursor,
                    replay_request_id,
                    terminal_override=(event_type, occurred_at),
                )

    def get_request_status(
        self,
        replay_request_id: str,
        *,
        tenant_id: str,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                self._set_search_path(cursor)
                self._set_tenant(cursor, tenant_id)
                return self._request_status(cursor, replay_request_id)

    def _request_status(
        self,
        cursor: Any,
        replay_request_id: str,
        *,
        terminal_override: tuple[str, datetime] | None = None,
    ) -> dict[str, Any]:
        cursor.execute(
            """
            SELECT
                request.legal_provider_replay_request_id,
                request.legal_provider_dead_letter_id,
                event.event_type,
                request.requested_at,
                event.recorded_at,
                (request.legal_provider_replay_object_id IS NOT NULL)
            FROM legal_provider_replay_requests AS request
            JOIN LATERAL (
                SELECT event_type, recorded_at
                FROM legal_provider_replay_events
                WHERE legal_provider_replay_request_id =
                    request.legal_provider_replay_request_id
                ORDER BY
                    sequence_no DESC,
                    legal_provider_replay_event_id DESC
                LIMIT 1
            ) AS event ON TRUE
            WHERE request.legal_provider_replay_request_id = %s
              AND request.tenant_id = NULLIF(
                  current_setting('realytics.tenant_id', true),
                  ''
              )
            """,
            (replay_request_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ReplayRecordNotFound(replay_request_id)
        state = row[2]
        updated_at = row[4]
        if terminal_override is not None:
            state, updated_at = terminal_override
        return _request_projection(
            request_id=row[0],
            dead_letter_id=row[1],
            status=ProviderReplayStatus(state),
            requested_at=row[3],
            updated_at=updated_at,
            object_reference_present=bool(row[5]),
        )

    @staticmethod
    def _latest_event(
        cursor: Any,
        replay_request_id: str,
    ) -> tuple[int, str]:
        cursor.execute(
            """
            SELECT sequence_no, event_type
            FROM legal_provider_replay_events
            WHERE legal_provider_replay_request_id = %s
            ORDER BY sequence_no DESC, legal_provider_replay_event_id DESC
            LIMIT 1
            """,
            (replay_request_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ReplayStateConflict(
                "replay request has no lifecycle event"
            )
        return int(row[0]), str(row[1])

    def _set_search_path(self, cursor: Any) -> None:
        cursor.execute(
            f"SET LOCAL search_path TO {self._schema}, public, pg_temp"
        )

    @staticmethod
    def _set_tenant(cursor: Any, tenant_id: str) -> None:
        cursor.execute(
            "SELECT set_config('realytics.tenant_id', %s, true)",
            (tenant_id,),
        )

    @staticmethod
    def _lock_aggregate(cursor: Any, aggregate_key: str) -> None:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (aggregate_key,),
        )


def _approval_projection(
    *,
    approval_event_id: str,
    dead_letter_id: str,
    project_id: str,
    replay_object_id: str,
    approved_requester_id: str,
    approved_at: datetime,
    expires_at: datetime,
    supersedes_approval_event_id: str | None,
) -> dict[str, Any]:
    return {
        "approvalEventId": approval_event_id,
        "deadLetterId": dead_letter_id,
        "projectId": project_id,
        "replayObjectId": replay_object_id,
        "approvedRequesterId": approved_requester_id,
        "approvedAt": _json_datetime(approved_at),
        "expiresAt": _json_datetime(expires_at),
        "supersedesApprovalEventId": supersedes_approval_event_id,
        "consumed": False,
        "objectBindingPresent": True,
    }


def _dead_letter_projection(row: Any) -> dict[str, Any]:
    request = None
    if row[7] is not None:
        request = _request_projection(
            request_id=row[7],
            dead_letter_id=row[0],
            status=ProviderReplayStatus(str(row[8])),
            requested_at=row[9],
            updated_at=row[10],
            object_reference_present=bool(row[11]),
        )
    return {
        "deadLetterId": row[0],
        "providerId": row[1],
        "operation": row[2],
        "reasonCode": row[3],
        "attempts": row[4],
        "target": row[5],
        "occurredAt": _json_datetime(row[6]),
        "replayStatus": str(row[8]),
        "latestReplayRequest": request,
        "replayObjectReceiptPresent": bool(row[12]),
        "replayable": bool(row[12]),
    }


def _replay_object_projection(
    *,
    replay_object_id: str,
    dead_letter_id: str,
    project_id: str,
    provider_id: str,
    object_version_id: str,
    retention_until: datetime,
    registered_at: datetime | None,
) -> dict[str, Any]:
    return {
        "replayObjectId": replay_object_id,
        "deadLetterId": dead_letter_id,
        "projectId": project_id,
        "providerId": provider_id,
        "objectVersionId": object_version_id,
        "retentionUntil": _json_datetime(retention_until),
        "registeredAt": _json_datetime(registered_at),
        "objectBindingPresent": True,
    }


def _request_projection(
    *,
    request_id: str,
    dead_letter_id: str,
    status: ProviderReplayStatus,
    requested_at: datetime,
    updated_at: datetime,
    object_reference_present: bool,
) -> dict[str, Any]:
    return {
        "replayRequestId": request_id,
        "deadLetterId": dead_letter_id,
        "status": status.value,
        "requestedAt": _json_datetime(requested_at),
        "updatedAt": _json_datetime(updated_at),
        "objectReferencePresent": object_reference_present,
    }


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _json_datetime(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _aware(value).isoformat()
    return str(value)


def _locator_sha256(locator: str) -> str:
    digest = hashlib.sha256(str(locator).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _locator_binds_dead_letter(
    locator: str,
    dead_letter_id: str,
) -> bool:
    # Keep this byte-for-byte path identity contract aligned with the
    # PostgreSQL CHECK constraint.  Percent-encoded identifiers are not
    # decoded into a different object identity.
    path = urlsplit(str(locator)).path
    identity = str(dead_letter_id).strip().casefold()
    leaf = path.rsplit("/", 1)[-1].casefold()
    return bool(
        identity
        and leaf in {
            identity,
            f"{identity}.json",
        }
    )


__all__ = [
    "PostgresLegalProviderReplayRepository",
    "ProviderReplayMaterial",
    "ReplayApprovalConsumed",
    "ReplayApprovalExpired",
    "ReplayContractConflict",
    "ReplayRecordNotFound",
    "ReplayStateConflict",
]
