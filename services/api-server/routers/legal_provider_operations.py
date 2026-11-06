"""Restricted operational API for legal-provider dead-letter replay."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request

from core.authz import authorize_request, is_explicit_human_principal
from db.legal_operations_database import (
    legal_operations_database_configured,
)
from db.postgres import postgres_configured
from db.repositories.legal_provider_replay_repository import (
    PostgresLegalProviderReplayRepository,
    ReplayApprovalConsumed,
    ReplayApprovalExpired,
    ReplayContractConflict,
    ReplayRecordNotFound,
    ReplayStateConflict,
)
from legal_intelligence.provider_replay_models import (
    LegalProviderReplayApprovalInput,
    LegalProviderReplayRequestInput,
    ProviderReplayStatus,
)
from use_cases.legal_provider_replay import LegalProviderReplayService


router = APIRouter(
    prefix="/ops/legal-provider/dead-letters",
    tags=["legal-provider-operations"],
)

_OPERATOR_ROLES = frozenset({"admin", "compliance_officer"})


@router.get("")
def list_legal_provider_dead_letters(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    provider_id: str | None = Query(
        default=None,
        alias="providerId",
        min_length=1,
        max_length=120,
    ),
    status: ProviderReplayStatus | None = None,
) -> dict[str, Any]:
    service, _actor, _role, tenant_id = _service(request)
    return service.list_dead_letters(
        tenant_id=tenant_id,
        limit=limit,
        provider_id=provider_id,
        status=status,
    )


@router.post("/{dead_letter_id}/replay-approvals", status_code=201)
def approve_legal_provider_replay_object(
    dead_letter_id: str,
    request: Request,
    payload: LegalProviderReplayApprovalInput,
) -> dict[str, Any]:
    service, actor, role, tenant_id = _service(request)
    try:
        return service.approve_replay_object(
            dead_letter_id,
            payload,
            actor_id=actor,
            actor_role=role,
            tenant_id=tenant_id,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "legal_provider_replay_approver_required"
            },
        ) from exc
    except ReplayRecordNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "legal_provider_dead_letter_not_found"},
        ) from exc
    except ReplayContractConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "legal_provider_replay_approval_conflict",
                "message": str(exc),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_legal_provider_replay_approval",
                "message": str(exc),
            },
        ) from exc


@router.post("/{dead_letter_id}/replay-requests", status_code=202)
def request_legal_provider_replay(
    dead_letter_id: str,
    request: Request,
    payload: LegalProviderReplayRequestInput,
    idempotency_key: str = Header(
        alias="X-Idempotency-Key",
        min_length=1,
        max_length=128,
    ),
) -> dict[str, Any]:
    service, actor, role, tenant_id = _service(request)
    try:
        return service.request_replay(
            dead_letter_id,
            payload,
            actor_id=actor,
            actor_role=role,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={"error": "legal_provider_operator_required"},
        ) from exc
    except ReplayRecordNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "legal_provider_dead_letter_not_found"},
        ) from exc
    except (
        ReplayApprovalConsumed,
        ReplayApprovalExpired,
        ReplayContractConflict,
        ReplayStateConflict,
    ) as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "legal_provider_replay_contract_conflict",
                "message": str(exc),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_legal_provider_replay_request",
                "message": str(exc),
            },
        ) from exc


def _service(
    request: Request,
) -> tuple[LegalProviderReplayService, str, str, str]:
    authorization = authorize_request(request, "legal:operate")
    if not authorization.get("authenticated"):
        raise HTTPException(
            status_code=401,
            detail={"error": "authentication_required"},
        )
    user = authorization.get("user") or {}
    actor = str(user.get("userId") or "").strip()
    tenant_id = str(user.get("tenantId") or "").strip()
    role = str(
        authorization.get("role")
        or user.get("role")
        or ""
    ).strip().casefold()
    if (
        role not in _OPERATOR_ROLES
        or not is_explicit_human_principal(authorization)
        or not actor
        or not tenant_id
        or actor.casefold() in {"anonymous", "system", "unknown"}
    ):
        raise HTTPException(
            status_code=403,
            detail={"error": "legal_provider_operator_required"},
        )
    if (
        not postgres_configured()
        or not legal_operations_database_configured()
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "error": "normalized_legal_operations_store_required",
                "message": (
                    "Legal provider DLQ operations require normalized "
                    "PostgreSQL and a dedicated legal-operations DSN; no "
                    "runtime-role, JSON, or in-memory authority fallback is "
                    "permitted."
                ),
            },
        )
    return (
        LegalProviderReplayService(
            PostgresLegalProviderReplayRepository(),
        ),
        actor,
        role,
        tenant_id,
    )


__all__ = ["router"]
