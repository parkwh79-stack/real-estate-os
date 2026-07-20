from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from db.repositories.legal_provider_replay_repository import (
    PostgresLegalProviderReplayRepository,
    ProviderReplayMaterial,
    ReplayApprovalConsumed,
    ReplayApprovalExpired,
    ReplayContractConflict,
)
from legal_intelligence.provider_replay_models import (
    LegalProviderReplayApprovalInput,
    LegalProviderReplayObjectReceipt,
    LegalProviderReplayRequestInput,
    ProviderReplayStatus,
)
from routers import legal_provider_operations
from use_cases.legal_provider_replay import (
    LegalProviderReplayObjectRegistrar,
    LegalProviderReplayService,
    ProviderReplayExecutionResult,
)


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
EXPIRES = NOW + timedelta(hours=1)
OBJECT_SHA = "sha256:" + "a" * 64
OBJECT_LOCATOR = "quarantine://legal-raw/provider/dlq-1.json"
OBJECT_VERSION_ID = "version-1"
REPLAY_OBJECT_ID = "legal-provider-replay-object:object-1"
OBJECT_RETENTION = NOW + timedelta(days=30)
RECEIPT_SHA = "sha256:" + "b" * 64
LOCATOR_SHA = "sha256:" + hashlib.sha256(
    OBJECT_LOCATOR.encode("utf-8")
).hexdigest()
APPROVAL_ID = "legal-provider-replay-approval:approval-1"


class _Cursor:
    def __init__(
        self,
        statements: list[tuple[str, object]],
        *,
        rows=(),
        one=(),
    ) -> None:
        self.statements = statements
        self.rows = list(rows)
        self.one = list(one)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, params=None) -> None:
        self.statements.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.one.pop(0) if self.one else None


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


def _approval_payload(**overrides) -> LegalProviderReplayApprovalInput:
    values = {
        "projectId": "project-1",
        "replayObjectId": REPLAY_OBJECT_ID,
        "replayRequesterId": "operator-1",
        "expiresAt": EXPIRES.isoformat(),
        "idempotencyKey": "legal-dlq-approval:1",
    }
    values.update(overrides)
    return LegalProviderReplayApprovalInput.model_validate(values)


def _request_payload(**overrides) -> LegalProviderReplayRequestInput:
    values = {
        "approvalEventId": APPROVAL_ID,
    }
    values.update(overrides)
    return LegalProviderReplayRequestInput.model_validate(values)


def _object_receipt(**overrides) -> LegalProviderReplayObjectReceipt:
    values = {
        "tenantId": "tenant-1",
        "projectId": "project-1",
        "deadLetterId": "dlq-1",
        "providerId": "law-go-kr",
        "objectLocator": OBJECT_LOCATOR,
        "objectVersionId": OBJECT_VERSION_ID,
        "objectSha256": OBJECT_SHA,
        "objectLockMode": "compliance",
        "retentionUntil": OBJECT_RETENTION.isoformat(),
        "objectStoreReceiptSha256": RECEIPT_SHA,
        "registeredBy": "object-worker-1",
        "idempotencyKey": "object-receipt:1",
    }
    values.update(overrides)
    return LegalProviderReplayObjectReceipt.model_validate(values)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/ops/legal-provider/dead-letters",
            "headers": [],
            "query_string": b"",
        }
    )


def _approval_row(
    *,
    requester="operator-1",
    approver="compliance-2",
    expires=EXPIRES,
    successor=None,
    consumed=None,
    locator_sha=LOCATOR_SHA,
    retention=OBJECT_RETENTION,
):
    return (
        "project-1",
        REPLAY_OBJECT_ID,
        "law-go-kr",
        OBJECT_LOCATOR,
        locator_sha,
        OBJECT_VERSION_ID,
        OBJECT_SHA,
        retention,
        RECEIPT_SHA,
        requester,
        approver,
        NOW,
        expires,
        successor,
        consumed,
    )


def test_request_accepts_only_server_issued_approval_event_reference() -> None:
    assert _request_payload().model_dump() == {
        "approval_event_id": APPROVAL_ID
    }
    with pytest.raises(ValidationError):
        _request_payload(idempotencyKey="body-idempotency-is-forbidden")
    with pytest.raises(ValidationError):
        LegalProviderReplayRequestInput.model_validate(
            {
                "approvedObjectLocator": OBJECT_LOCATOR,
                "approvedObjectSha256": OBJECT_SHA,
                "objectReferenceApprovedBy": "spoofed-approver",
                "idempotencyKey": "legacy:1",
            }
        )
    with pytest.raises(ValidationError):
        _request_payload(approvalEventId="unsafe approval/id")


def test_catalog_receipt_rejects_signed_urls_credentials_bad_hash_and_naive_retention():
    with pytest.raises(ValidationError):
        _object_receipt(
            objectLocator=(
                "https://bucket.example/object?X-Amz-Credential=secret"
            )
        )
    with pytest.raises(ValidationError):
        _object_receipt(
            objectLocator="s3://user:secret@bucket/object"
        )
    with pytest.raises(ValidationError):
        _object_receipt(objectSha256="sha256:not-a-digest")
    with pytest.raises(ValidationError):
        _object_receipt(retentionUntil="2026-07-20T13:00:00")
    with pytest.raises(ValidationError):
        _approval_payload(expiresAt="2026-07-20T13:00:00")
    receipt = _object_receipt(
        objectVersionId="3/L4kqtJlcpXro+MTRCxf3vjVBH40Nr8X8g=="
    )
    assert "/" in receipt.object_version_id
    with pytest.raises(ValidationError):
        _object_receipt(objectVersionId="version with whitespace")


def test_approval_api_accepts_catalog_id_and_rejects_caller_object_claims():
    assert _approval_payload().model_dump()["replay_object_id"] == REPLAY_OBJECT_ID
    with pytest.raises(ValidationError):
        _approval_payload(approvedObjectLocator=OBJECT_LOCATOR)
    with pytest.raises(ValidationError):
        _approval_payload(approvedObjectSha256=OBJECT_SHA)


def test_repository_list_is_tenant_scoped_and_redacts_object_locator() -> None:
    statements: list[tuple[str, object]] = []
    cursor = _Cursor(
        statements,
        rows=[
            (
                "dlq-1",
                "law-go-kr",
                "search",
                "HTTP_503",
                3,
                "law",
                NOW,
                "replay-1",
                "requested",
                NOW,
                NOW,
                True,
                True,
            )
        ],
    )
    repository = PostgresLegalProviderReplayRepository(
        connection_factory=lambda: _Connection(cursor),
        schema_name="realytics",
    )

    items = repository.list_dead_letters(
        tenant_id="tenant-1",
        limit=10,
        provider_id="law-go-kr",
        status=ProviderReplayStatus.REQUESTED,
    )

    assert items[0]["replayStatus"] == "requested"
    assert items[0]["replayObjectReceiptPresent"] is True
    rendered = repr(items).casefold()
    assert "objectlocator" not in rendered
    assert "quarantine://" not in rendered
    list_query = next(
        (sql, params)
        for sql, params in statements
        if "FROM legal_provider_dead_letters" in sql
    )
    assert "r.tenant_id = %s" in list_query[0]
    assert list_query[1][0] == "tenant-1"


def test_trusted_worker_registers_exact_immutable_object_receipt() -> None:
    statements: list[tuple[str, object]] = []
    cursor = _Cursor(statements, one=[("law-go-kr",), None])
    repository = PostgresLegalProviderReplayRepository(
        connection_factory=lambda: _Connection(cursor),
        schema_name="realytics",
        clock=lambda: NOW,
        id_factory=lambda: "object-1",
    )

    result = LegalProviderReplayObjectRegistrar(
        repository,
        clock=lambda: NOW,
    ).register(_object_receipt())

    assert result["replayObjectId"] == REPLAY_OBJECT_ID
    assert result["objectBindingPresent"] is True
    assert OBJECT_LOCATOR not in repr(result)
    assert OBJECT_SHA not in repr(result)
    insert = next(
        (sql, params)
        for sql, params in statements
        if sql.startswith("INSERT INTO legal_provider_replay_objects")
    )
    assert insert[1][1:5] == (
        "tenant-1",
        "project-1",
        "dlq-1",
        "law-go-kr",
    )
    assert insert[1][6:9] == (
        LOCATOR_SHA,
        OBJECT_VERSION_ID,
        OBJECT_SHA,
    )


def test_repository_appends_immutable_authenticated_approval_event() -> None:
    statements: list[tuple[str, object]] = []
    cursor = _Cursor(
        statements,
        one=[
            (
                "law-go-kr",
                OBJECT_LOCATOR,
                LOCATOR_SHA,
                OBJECT_VERSION_ID,
                OBJECT_SHA,
                OBJECT_RETENTION,
                RECEIPT_SHA,
                NOW,
            ),
            None,
        ],
    )
    repository = PostgresLegalProviderReplayRepository(
        connection_factory=lambda: _Connection(cursor),
        schema_name="realytics",
        clock=lambda: NOW,
        id_factory=lambda: "approval-1",
    )

    result = repository.create_replay_approval(
        "dlq-1",
        tenant_id="tenant-1",
        project_id="project-1",
        replay_object_id=REPLAY_OBJECT_ID,
        replay_requester_id="operator-1",
        approved_by="compliance-2",
        approver_role="compliance_officer",
        approved_at=NOW,
        expires_at=EXPIRES,
        idempotency_key="approval:1",
        supersedes_approval_event_id=None,
    )

    assert result["approvalEventId"] == APPROVAL_ID
    assert result["consumed"] is False
    rendered = repr(result)
    assert OBJECT_LOCATOR not in rendered
    assert OBJECT_SHA not in rendered
    insert = next(
        (sql, params)
        for sql, params in statements
        if sql.startswith(
            "INSERT INTO legal_provider_replay_approval_events"
        )
    )
    assert insert[1][1:6] == (
        "tenant-1",
        "project-1",
        "dlq-1",
        REPLAY_OBJECT_ID,
        "law-go-kr",
    )
    assert insert[1][7] == LOCATOR_SHA
    assert insert[1][13] == "compliance-2"
    assert any(
        "set_config('realytics.tenant_id'" in sql
        for sql, _params in statements
    )


def test_repository_refuses_approval_without_catalog_object_receipt() -> None:
    statements: list[tuple[str, object]] = []
    repository = PostgresLegalProviderReplayRepository(
        connection_factory=lambda: _Connection(
            _Cursor(statements, one=[None])
        ),
        schema_name="realytics",
        clock=lambda: NOW,
    )

    with pytest.raises(
        ReplayContractConflict,
        match="immutable replay object receipt",
    ):
        repository.create_replay_approval(
            "dlq-1",
            tenant_id="tenant-1",
            project_id="project-1",
            replay_object_id=REPLAY_OBJECT_ID,
            replay_requester_id="operator-1",
            approved_by="compliance-2",
            approver_role="compliance_officer",
            approved_at=NOW,
            expires_at=EXPIRES,
            idempotency_key="approval:missing-receipt",
            supersedes_approval_event_id=None,
        )

    assert not any(
        sql.startswith("INSERT INTO legal_provider_replay_approval_events")
        for sql, _params in statements
    )


def test_repository_serializes_supersession_before_dead_letter_scope() -> None:
    statements: list[tuple[str, object]] = []
    previous_id = "legal-provider-replay-approval:previous"
    cursor = _Cursor(
        statements,
        one=[
            (
                "law-go-kr",
                OBJECT_LOCATOR,
                LOCATOR_SHA,
                OBJECT_VERSION_ID,
                OBJECT_SHA,
                OBJECT_RETENTION,
                RECEIPT_SHA,
                NOW,
            ),
            None,
            (previous_id,),
        ],
    )
    repository = PostgresLegalProviderReplayRepository(
        connection_factory=lambda: _Connection(cursor),
        schema_name="realytics",
        clock=lambda: NOW,
        id_factory=lambda: "approval-2",
    )

    repository.create_replay_approval(
        "dlq-1",
        tenant_id="tenant-1",
        project_id="project-1",
        replay_object_id=REPLAY_OBJECT_ID,
        replay_requester_id="operator-1",
        approved_by="compliance-2",
        approver_role="compliance_officer",
        approved_at=NOW,
        expires_at=EXPIRES,
        idempotency_key="approval:2",
        supersedes_approval_event_id=previous_id,
    )

    lock_keys = [
        params[0]
        for sql, params in statements
        if "pg_advisory_xact_lock" in sql
    ]
    assert lock_keys == [
        f"legal-provider-replay-approval:{previous_id}",
        "dead-letter:dlq-1",
        (
            "legal-provider-replay-approval:"
            "tenant-1:project-1:dlq-1:operator-1"
        ),
    ]


def test_repository_consumes_current_same_scope_approval_once() -> None:
    statements: list[tuple[str, object]] = []
    cursor = _Cursor(statements, one=[None, _approval_row()])
    repository = PostgresLegalProviderReplayRepository(
        connection_factory=lambda: _Connection(cursor),
        schema_name="realytics",
        clock=lambda: NOW,
        id_factory=lambda: "request-1",
    )

    result = repository.request_replay(
        "dlq-1",
        tenant_id="tenant-1",
        approval_event_id=APPROVAL_ID,
        idempotency_key="request:1",
        requested_by="operator-1",
        requester_role="admin",
    )

    assert result["status"] == "requested"
    request_insert = next(
        (sql, params)
        for sql, params in statements
        if sql.startswith("INSERT INTO legal_provider_replay_requests")
    )
    assert request_insert[1][1:5] == (
        APPROVAL_ID,
        "tenant-1",
        "project-1",
        "dlq-1",
    )
    assert request_insert[1][5:10] == (
        REPLAY_OBJECT_ID,
        "law-go-kr",
        OBJECT_LOCATOR,
        OBJECT_VERSION_ID,
        OBJECT_SHA,
    )
    assert request_insert[1][12] == "compliance-2"
    assert request_insert[1][15] == "operator-1"
    assert not any(
        "approved_object_locator" in str(params)
        and params == (APPROVAL_ID,)
        for _sql, params in statements
    )


@pytest.mark.parametrize(
    ("approval", "error_type", "message"),
    [
        (
            _approval_row(expires=NOW),
            ReplayApprovalExpired,
            "expired",
        ),
        (
            _approval_row(consumed="other-request"),
            ReplayApprovalConsumed,
            "consumed",
        ),
        (
            _approval_row(successor="new-approval"),
            ReplayContractConflict,
            "superseded",
        ),
        (
            _approval_row(requester="other-user"),
            ReplayContractConflict,
            "approved requester",
        ),
        (
            _approval_row(locator_sha="sha256:" + "b" * 64),
            ReplayContractConflict,
            "locator binding",
        ),
        (
            _approval_row(retention=NOW),
            ReplayApprovalExpired,
            "retention",
        ),
    ],
)
def test_repository_fails_closed_for_invalid_approval_state(
    approval,
    error_type,
    message,
) -> None:
    statements: list[tuple[str, object]] = []
    cursor = _Cursor(statements, one=[None, approval])
    repository = PostgresLegalProviderReplayRepository(
        connection_factory=lambda: _Connection(cursor),
        schema_name="realytics",
        clock=lambda: NOW,
    )

    with pytest.raises(error_type, match=message):
        repository.request_replay(
            "dlq-1",
            tenant_id="tenant-1",
            approval_event_id=APPROVAL_ID,
            idempotency_key="request:invalid",
            requested_by="operator-1",
            requester_role="admin",
        )

    assert not any(
        sql.startswith("INSERT INTO legal_provider_replay_requests")
        for sql, _params in statements
    )


def _begin_row(*, catalog_version=OBJECT_VERSION_ID, retention=OBJECT_RETENTION):
    return (
        "replay-1",
        "dlq-1",
        "tenant-1",
        "project-1",
        "law-go-kr",
        "search",
        "law",
        REPLAY_OBJECT_ID,
        OBJECT_LOCATOR,
        OBJECT_VERSION_ID,
        OBJECT_SHA,
        retention,
        RECEIPT_SHA,
        REPLAY_OBJECT_ID,
        "law-go-kr",
        OBJECT_LOCATOR,
        LOCATOR_SHA,
        OBJECT_VERSION_ID,
        OBJECT_SHA,
        retention,
        RECEIPT_SHA,
        "law-go-kr",
        OBJECT_LOCATOR,
        LOCATOR_SHA,
        catalog_version,
        OBJECT_SHA,
        retention,
        RECEIPT_SHA,
    )


def test_repository_begin_replay_revalidates_exact_catalog_binding() -> None:
    statements: list[tuple[str, object]] = []
    cursor = _Cursor(
        statements,
        one=[_begin_row(), (1, "requested")],
    )
    repository = PostgresLegalProviderReplayRepository(
        connection_factory=lambda: _Connection(cursor),
        schema_name="realytics",
        clock=lambda: NOW,
        id_factory=lambda: "running-1",
    )

    material = repository.begin_replay(
        "replay-1",
        worker_id="worker-1",
        tenant_id="tenant-1",
    )

    assert material.replay_object_id == REPLAY_OBJECT_ID
    assert material.approved_object_version_id == OBJECT_VERSION_ID
    assert material.approved_object_sha256 == OBJECT_SHA
    assert any(
        sql.startswith("INSERT INTO legal_provider_replay_events")
        and params[3] == "worker-1"
        for sql, params in statements
    )


@pytest.mark.parametrize(
    "row",
    [
        _begin_row(catalog_version="different-version"),
        _begin_row(retention=NOW),
    ],
)
def test_repository_begin_replay_fails_closed_for_catalog_drift_or_expiry(
    row,
) -> None:
    statements: list[tuple[str, object]] = []
    repository = PostgresLegalProviderReplayRepository(
        connection_factory=lambda: _Connection(
            _Cursor(statements, one=[row])
        ),
        schema_name="realytics",
        clock=lambda: NOW,
    )

    with pytest.raises(ReplayContractConflict, match="catalog object receipt"):
        repository.begin_replay(
            "replay-1",
            worker_id="worker-1",
            tenant_id="tenant-1",
        )

    assert not any(
        sql.startswith("INSERT INTO legal_provider_replay_events")
        for sql, _params in statements
    )


def test_repository_rejects_object_key_not_bound_to_dead_letter() -> None:
    repository = PostgresLegalProviderReplayRepository(
        connection_factory=lambda: pytest.fail(
            "unbound approval must fail before opening PostgreSQL"
        ),
        schema_name="realytics",
    )

    with pytest.raises(
        ReplayContractConflict,
        match="not exactly bound",
    ):
        repository.register_replay_object(
            _object_receipt(
                objectLocator=(
                    "quarantine://legal-raw/provider/different-dlq.json"
                )
            )
        )


def test_locator_binding_does_not_decode_a_different_object_identity() -> None:
    repository = PostgresLegalProviderReplayRepository(
        connection_factory=lambda: pytest.fail(
            "percent-encoded identity must fail before opening PostgreSQL"
        ),
        schema_name="realytics",
    )

    with pytest.raises(
        ReplayContractConflict,
        match="not exactly bound",
    ):
        repository.register_replay_object(
            _object_receipt(
                objectLocator=(
                    "quarantine://legal-raw/provider/%64lq-1.json"
                )
            )
        )


def test_locator_binding_rejects_dead_letter_substring_collision() -> None:
    repository = PostgresLegalProviderReplayRepository(
        connection_factory=lambda: pytest.fail(
            "substring collision must fail before opening PostgreSQL"
        ),
        schema_name="realytics",
    )

    with pytest.raises(
        ReplayContractConflict,
        match="not exactly bound",
    ):
        repository.register_replay_object(
            _object_receipt(
                objectLocator=(
                    "quarantine://legal-raw/provider/dlq-10.json"
                )
            )
        )


class _ReplayRepository:
    def __init__(self) -> None:
        self.approval_args = None
        self.request_args = None
        self.outcomes = []

    def list_dead_letters(self, **_kwargs):
        return []

    def register_replay_object(self, receipt):
        return {
            "replayObjectId": REPLAY_OBJECT_ID,
            "deadLetterId": receipt.dead_letter_id,
        }

    def create_replay_approval(self, dead_letter_id, **kwargs):
        self.approval_args = (dead_letter_id, kwargs)
        return {
            "approvalEventId": APPROVAL_ID,
            "deadLetterId": dead_letter_id,
            "expiresAt": EXPIRES.isoformat(),
            "objectBindingPresent": True,
        }

    def request_replay(self, dead_letter_id, **kwargs):
        self.request_args = (dead_letter_id, kwargs)
        return {
            "replayRequestId": "replay-1",
            "deadLetterId": dead_letter_id,
            "status": "requested",
            "objectReferencePresent": True,
        }

    def begin_replay(
        self,
        replay_request_id,
        *,
        worker_id,
        tenant_id,
    ):
        assert worker_id == "worker-1"
        assert tenant_id == "tenant-1"
        return ProviderReplayMaterial(
            replay_request_id=replay_request_id,
            dead_letter_id="dlq-1",
            tenant_id=tenant_id,
            project_id="project-1",
            provider_id="law-go-kr",
            operation="search",
            target="law",
            replay_object_id=REPLAY_OBJECT_ID,
            approved_object_locator=OBJECT_LOCATOR,
            approved_object_version_id=OBJECT_VERSION_ID,
            approved_object_sha256=OBJECT_SHA,
            object_retention_until=OBJECT_RETENTION,
        )

    def record_outcome(self, replay_request_id, **kwargs):
        self.outcomes.append((replay_request_id, kwargs))
        return {
            "replayRequestId": replay_request_id,
            "status": "completed" if kwargs["completed"] else "failed",
            "objectReferencePresent": True,
        }


def test_service_separates_authenticated_approver_and_requester() -> None:
    repository = _ReplayRepository()
    service = LegalProviderReplayService(
        repository,
        clock=lambda: NOW,
    )

    with pytest.raises(PermissionError):
        service.approve_replay_object(
            "dlq-1",
            _approval_payload(),
            actor_id="admin-1",
            actor_role="admin",
            tenant_id="tenant-1",
        )
    with pytest.raises(PermissionError):
        service.approve_replay_object(
            "dlq-1",
            _approval_payload(replayRequesterId="compliance-2"),
            actor_id="compliance-2",
            actor_role="compliance_officer",
            tenant_id="tenant-1",
        )

    approval = service.approve_replay_object(
        "dlq-1",
        _approval_payload(),
        actor_id="compliance-2",
        actor_role="compliance_officer",
        tenant_id="tenant-1",
    )
    replay = service.request_replay(
        "dlq-1",
        _request_payload(),
        actor_id="operator-1",
        actor_role="admin",
        tenant_id="tenant-1",
        idempotency_key="legal-dlq-request:1",
    )

    assert approval["approvalEventId"] == APPROVAL_ID
    assert replay["status"] == "requested"
    assert repository.approval_args[1]["approved_by"] == "compliance-2"
    assert repository.approval_args[1]["tenant_id"] == "tenant-1"
    assert (
        repository.approval_args[1]["replay_object_id"]
        == REPLAY_OBJECT_ID
    )
    assert "approved_object_locator" not in repository.approval_args[1]
    assert "approved_object_sha256" not in repository.approval_args[1]
    assert repository.request_args[1] == {
        "tenant_id": "tenant-1",
        "approval_event_id": APPROVAL_ID,
        "idempotency_key": "legal-dlq-request:1",
        "requested_by": "operator-1",
        "requester_role": "admin",
    }
    assert OBJECT_LOCATOR not in repr(replay)
    assert OBJECT_SHA not in repr(replay)


def test_service_rejects_expired_or_overlong_approval() -> None:
    service = LegalProviderReplayService(
        _ReplayRepository(),
        clock=lambda: NOW,
    )
    with pytest.raises(ValueError, match="future"):
        service.approve_replay_object(
            "dlq-1",
            _approval_payload(expiresAt=NOW.isoformat()),
            actor_id="compliance-2",
            actor_role="compliance_officer",
            tenant_id="tenant-1",
        )
    with pytest.raises(ValueError, match="24 hours"):
        service.approve_replay_object(
            "dlq-1",
            _approval_payload(
                expiresAt=(NOW + timedelta(hours=25)).isoformat()
            ),
            actor_id="compliance-2",
            actor_role="compliance_officer",
            tenant_id="tenant-1",
        )


def test_worker_records_success_with_exact_replay_binding() -> None:
    repository = _ReplayRepository()

    class Executor:
        def execute(self, material):
            return ProviderReplayExecutionResult(
                completed=True,
                outcome_code="REPLAY_COMPLETED",
                object_sha256_verified=True,
                verified_replay_request_id=material.replay_request_id,
                verified_replay_object_id=material.replay_object_id,
                verified_object_version_id=(
                    material.approved_object_version_id
                ),
                verified_object_sha256=material.approved_object_sha256,
                executor_reference="executor-run:42",
            )

    result = LegalProviderReplayService(
        repository,
        executor=Executor(),
    ).execute_requested_replay(
        "replay-1",
        worker_id="worker-1",
        tenant_id="tenant-1",
    )

    assert result["status"] == "completed"


def test_worker_discards_sensitive_exception_and_rejects_bad_binding() -> None:
    repository = _ReplayRepository()

    class BrokenExecutor:
        def execute(self, _material):
            raise RuntimeError("Housing Act raw query password=secret")

    result = LegalProviderReplayService(
        repository,
        executor=BrokenExecutor(),
    ).execute_requested_replay(
        "replay-1",
        worker_id="worker-1",
        tenant_id="tenant-1",
    )
    assert result["status"] == "failed"
    rendered = repr(repository.outcomes)
    assert "Housing Act" not in rendered
    assert "password" not in rendered
    assert "EXECUTOR_EXCEPTION" in rendered

    class MismatchedExecutor:
        def execute(self, material):
            return ProviderReplayExecutionResult(
                completed=True,
                outcome_code="REPLAY_COMPLETED",
                object_sha256_verified=True,
                verified_replay_request_id="different-replay",
                verified_replay_object_id=material.replay_object_id,
                verified_object_version_id=(
                    material.approved_object_version_id
                ),
                verified_object_sha256=material.approved_object_sha256,
            )

    LegalProviderReplayService(
        repository,
        executor=MismatchedExecutor(),
    ).execute_requested_replay(
        "replay-1",
        worker_id="worker-1",
        tenant_id="tenant-1",
    )
    assert (
        repository.outcomes[-1][1]["outcome_code"]
        == "REPLAY_BINDING_NOT_VERIFIED"
    )


@pytest.mark.parametrize("role", ["legal_reviewer", "viewer", "analyst"])
def test_operational_router_rejects_non_operator_roles(
    monkeypatch,
    role: str,
) -> None:
    monkeypatch.setattr(
        legal_provider_operations,
        "authorize_request",
        lambda *_args, **_kwargs: {
            "authenticated": True,
            "role": role,
            "user": {
                "userId": "user-1",
                "tenantId": "tenant-1",
                "role": role,
            },
        },
    )

    with pytest.raises(HTTPException) as error:
        legal_provider_operations._service(_request())

    assert error.value.status_code == 403


@pytest.mark.parametrize("role", ["admin", "compliance_officer"])
def test_operational_router_rejects_service_principal_with_human_role(
    monkeypatch,
    role: str,
) -> None:
    monkeypatch.setattr(
        legal_provider_operations,
        "authorize_request",
        lambda *_args, **_kwargs: {
            "authenticated": True,
            "role": role,
            "user": {
                "userId": "provider-worker-1",
                "tenantId": "tenant-1",
                "role": role,
                "metadata": {"principalType": "service"},
            },
        },
    )

    with pytest.raises(HTTPException) as error:
        legal_provider_operations._service(_request())

    assert error.value.status_code == 403


def test_operational_router_requires_tenant_and_normalized_postgres(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        legal_provider_operations,
        "authorize_request",
        lambda *_args, **_kwargs: {
            "authenticated": True,
            "role": "admin",
            "user": {
                "userId": "admin-1",
                "tenantId": "tenant-1",
                "role": "admin",
                "metadata": {"principalType": "human"},
            },
        },
    )
    monkeypatch.setattr(
        legal_provider_operations,
        "postgres_configured",
        lambda: False,
    )

    with pytest.raises(HTTPException) as error:
        legal_provider_operations._service(_request())

    assert error.value.status_code == 503
    assert (
        error.value.detail["error"]
        == "normalized_legal_operations_store_required"
    )


def test_operational_router_refuses_runtime_database_fallback(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        legal_provider_operations,
        "authorize_request",
        lambda *_args, **_kwargs: {
            "authenticated": True,
            "role": "admin",
            "user": {
                "userId": "admin-1",
                "tenantId": "tenant-1",
                "role": "admin",
                "metadata": {"principalType": "human"},
            },
        },
    )
    monkeypatch.setattr(
        legal_provider_operations,
        "postgres_configured",
        lambda: True,
    )
    monkeypatch.setattr(
        legal_provider_operations,
        "legal_operations_database_configured",
        lambda: False,
    )

    with pytest.raises(HTTPException) as error:
        legal_provider_operations._service(_request())

    assert error.value.status_code == 503
    assert "dedicated legal-operations DSN" in error.value.detail["message"]


def test_router_exposes_distinct_authenticated_approval_endpoint() -> None:
    paths = {
        route.path
        for route in legal_provider_operations.router.routes
    }
    assert (
        "/ops/legal-provider/dead-letters/"
        "{dead_letter_id}/replay-approvals"
    ) in paths
    assert (
        "/ops/legal-provider/dead-letters/"
        "{dead_letter_id}/replay-requests"
    ) in paths

    app = FastAPI()
    app.include_router(legal_provider_operations.router)
    schema = app.openapi()
    request_path = (
        "/ops/legal-provider/dead-letters/"
        "{dead_letter_id}/replay-requests"
    )
    operation = schema["paths"][request_path]["post"]
    header_names = {
        item["name"]
        for item in operation["parameters"]
        if item["in"] == "header"
    }
    assert "X-Idempotency-Key" in header_names
    request_schema = schema["components"]["schemas"][
        "LegalProviderReplayRequestInput"
    ]
    assert set(request_schema["properties"]) == {"approvalEventId"}
    assert request_schema["additionalProperties"] is False
    approval_schema = schema["components"]["schemas"][
        "LegalProviderReplayApprovalInput"
    ]
    assert "replayObjectId" in approval_schema["properties"]
    assert "approvedObjectLocator" not in approval_schema["properties"]
    assert "approvedObjectSha256" not in approval_schema["properties"]
    assert (
        "LegalProviderReplayObjectReceipt"
        not in schema["components"]["schemas"]
    )
