"""Tenant-native Legal Intelligence governance API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from core.authz import (
    authorize_request,
    enforce_high_risk_step_up,
    enforce_project_tenant_scope,
    is_explicit_human_principal,
)
from db.legal_review_database import (
    LegalReviewDatabaseConfigurationError,
    legal_review_database_configured,
)
from db.postgres import postgres_configured
from db.postgres_runtime_pool import api_tenant_transaction
from db.repositories.legal_governance_repository import (
    PostgresLegalGovernanceRepository,
)
from db.repositories.request_project_repository import current_project_scope
from legal_intelligence.governance_models import (
    CounselOpinionVersionInput,
    LegalAnalysisReviewInput,
    LegalAnalysisVersionInput,
    LegalApprovalDelegationInput,
    LegalApprovalDelegationRevokeInput,
    LegalAnnotationInput,
    LegalMatterVersionInput,
    LegalRequiredClaimSetInput,
    LegalWaiverCureInput,
    LegalWaiverVersionInput,
    TenantLegalTermAliasInput,
    TenantLegalTermAliasReviewInput,
)
from storage import get_project_record, storage_backend


router = APIRouter(
    prefix="/projects/{project_id}/legal",
    tags=["legal-governance"],
)


class _ApprovalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    validFrom: datetime | None = None
    legalApprovalDelegationId: str | None = None


class _DelegatedApprovalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    legalApprovalDelegationId: str | None = None


@router.post("/approval-delegations", status_code=201)
def grant_legal_approval_delegation(
    project_id: str,
    request: Request,
    payload: LegalApprovalDelegationInput,
) -> dict[str, Any]:
    repository, actor, role = _repository(
        project_id,
        request,
        permission="legal:review",
        step_up_action="legal_approval_delegation_grant",
    )
    return _created(
        "legalApprovalDelegationId",
        lambda: repository.grant_approval_delegation(
            payload,
            actor_id=actor,
            actor_role=role,
        ),
    )


@router.post(
    "/approval-delegations/{delegation_id}/revoke",
    status_code=201,
)
def revoke_legal_approval_delegation(
    project_id: str,
    delegation_id: str,
    request: Request,
    payload: LegalApprovalDelegationRevokeInput,
) -> dict[str, Any]:
    repository, actor, role = _repository(
        project_id,
        request,
        permission="legal:review",
        step_up_action="legal_approval_delegation_revoke",
    )
    return _created(
        "legalApprovalDelegationId",
        lambda: repository.revoke_approval_delegation(
            delegation_id,
            payload,
            actor_id=actor,
            actor_role=role,
        ),
    )


@router.get("/workspace")
def read_legal_workspace(
    project_id: str,
    request: Request,
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    repository, _actor, _role = _repository(
        project_id,
        request,
        permission="project:read",
        read_only=True,
    )
    try:
        workspace = repository.list_workspace(limit=limit)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "normalized_legal_store_unavailable",
                "message": "서버에서 프로젝트 법률 기록을 불러오지 못했습니다. 다시 시도해 주세요.",
            },
        ) from exc
    return {
        "schemaVersion": "realytics-legal-governance-workspace-v1",
        "projectId": project_id,
        "source": "normalized_postgres",
        "reviewWriteAvailable": legal_review_database_configured(),
        **workspace,
    }


@router.post("/matters", status_code=201)
def append_legal_matter(
    project_id: str,
    request: Request,
    payload: LegalMatterVersionInput,
) -> dict[str, Any]:
    repository, actor, role = _repository(
        project_id,
        request,
        permission="legal:review",
    )
    return _created(
        "projectLegalMatterId",
        lambda: repository.append_matter(
            payload,
            actor_id=actor,
            actor_role=role,
        ),
    )


@router.post("/required-claim-sets", status_code=201)
def propose_legal_required_claim_set(
    project_id: str,
    request: Request,
    payload: LegalRequiredClaimSetInput,
) -> dict[str, Any]:
    repository, actor, _role = _repository(
        project_id,
        request,
        permission="legal:review",
    )
    return _created(
        "legalRequiredClaimSetId",
        lambda: repository.propose_required_claim_set(
            payload,
            actor_id=actor,
        ),
    )


@router.post(
    "/required-claim-sets/{claim_set_id}/approve",
    status_code=201,
)
def approve_legal_required_claim_set(
    project_id: str,
    claim_set_id: str,
    request: Request,
    payload: _DelegatedApprovalPayload,
) -> dict[str, Any]:
    repository, actor, role = _repository(
        project_id,
        request,
        permission="legal:review",
        step_up_action="legal_required_claim_set_approve",
    )
    return _created(
        "legalRequiredClaimSetId",
        lambda: repository.approve_required_claim_set(
            claim_set_id,
            actor_id=actor,
            actor_role=role,
            legal_approval_delegation_id=(
                payload.legalApprovalDelegationId
            ),
        ),
    )


@router.post("/waivers", status_code=201)
def propose_legal_waiver(
    project_id: str,
    request: Request,
    payload: LegalWaiverVersionInput,
) -> dict[str, Any]:
    repository, actor, _role = _repository(
        project_id,
        request,
        permission="legal:review",
    )
    return _created(
        "legalWaiverId",
        lambda: repository.propose_waiver(payload, actor_id=actor),
    )


@router.post("/waivers/{waiver_id}/approve", status_code=201)
def approve_legal_waiver(
    project_id: str,
    waiver_id: str,
    request: Request,
    payload: _ApprovalPayload,
) -> dict[str, Any]:
    repository, actor, role = _repository(
        project_id,
        request,
        permission="legal:review",
        step_up_action="legal_waiver_approve",
    )
    return _created(
        "legalWaiverId",
        lambda: repository.approve_waiver(
            waiver_id,
            actor_id=actor,
            actor_role=role,
            valid_from=payload.validFrom,
            legal_approval_delegation_id=(
                payload.legalApprovalDelegationId
            ),
        ),
    )


@router.post("/waivers/{waiver_id}/cure", status_code=201)
def advance_legal_waiver_cure(
    project_id: str,
    waiver_id: str,
    request: Request,
    payload: LegalWaiverCureInput,
) -> dict[str, Any]:
    repository, actor, role = _repository(
        project_id,
        request,
        permission="legal:review",
        step_up_action="legal_waiver_cure",
    )
    return _created(
        "legalWaiverId",
        lambda: repository.advance_waiver_cure(
            waiver_id,
            payload,
            actor_id=actor,
            actor_role=role,
        ),
    )


@router.post("/counsel-opinions", status_code=201)
def append_counsel_opinion(
    project_id: str,
    request: Request,
    payload: CounselOpinionVersionInput,
) -> dict[str, Any]:
    repository, actor, role = _repository(
        project_id,
        request,
        permission="legal:review",
    )
    return _created(
        "counselOpinionId",
        lambda: repository.append_counsel_opinion(
            payload,
            actor_id=actor,
            actor_role=role,
        ),
    )


@router.post(
    "/counsel-opinions/{opinion_id}/approve",
    status_code=201,
)
def approve_counsel_opinion(
    project_id: str,
    opinion_id: str,
    request: Request,
    payload: _DelegatedApprovalPayload,
) -> dict[str, Any]:
    repository, actor, role = _repository(
        project_id,
        request,
        permission="legal:review",
        step_up_action="counsel_opinion_approve",
    )
    return _created(
        "counselOpinionId",
        lambda: repository.approve_counsel_opinion(
            opinion_id,
            actor_id=actor,
            actor_role=role,
            legal_approval_delegation_id=(
                payload.legalApprovalDelegationId
            ),
        ),
    )


@router.post("/analysis-versions", status_code=201)
def append_legal_analysis(
    project_id: str,
    request: Request,
    payload: LegalAnalysisVersionInput,
) -> dict[str, Any]:
    repository, actor, _role = _repository(
        project_id,
        request,
        permission="legal:review",
    )
    return _created(
        "legalAnalysisVersionId",
        lambda: repository.append_analysis(payload, actor_id=actor),
    )


@router.post(
    "/analysis-versions/{analysis_version_id}/review",
    status_code=201,
)
def review_legal_analysis(
    project_id: str,
    analysis_version_id: str,
    request: Request,
    payload: LegalAnalysisReviewInput,
) -> dict[str, Any]:
    repository, actor, role = _repository(
        project_id,
        request,
        permission="legal:review",
    )
    return _created(
        "legalAnalysisVersionId",
        lambda: repository.review_analysis(
            analysis_version_id,
            payload,
            actor_id=actor,
            actor_role=role,
        ),
    )


@router.post("/annotations", status_code=201)
def append_legal_annotation(
    project_id: str,
    request: Request,
    payload: LegalAnnotationInput,
) -> dict[str, Any]:
    repository, actor, _role = _repository(
        project_id,
        request,
        permission="legal:review",
    )
    return _created(
        "projectLegalAnnotationId",
        lambda: repository.append_annotation(payload, actor_id=actor),
    )


@router.post("/term-aliases", status_code=201)
def propose_tenant_legal_term_alias(
    project_id: str,
    request: Request,
    payload: TenantLegalTermAliasInput,
) -> dict[str, Any]:
    repository, actor, _role = _repository(
        project_id,
        request,
        permission="legal:review",
    )
    return _created(
        "tenantLegalTermAliasId",
        lambda: repository.propose_tenant_term_alias(
            payload,
            actor_id=actor,
        ),
    )


@router.post("/term-aliases/{alias_id}/review", status_code=201)
def review_tenant_legal_term_alias(
    project_id: str,
    alias_id: str,
    request: Request,
    payload: TenantLegalTermAliasReviewInput,
) -> dict[str, Any]:
    repository, actor, role = _repository(
        project_id,
        request,
        permission="legal:review",
    )
    try:
        result = repository.review_tenant_term_alias(
            alias_id,
            payload,
            actor_id=actor,
            actor_role=role,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={"error": "legal_glossary_reviewer_required"},
        ) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "legal_term_alias_not_found"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "legal_term_alias_review_rejected",
                "message": str(exc),
            },
        ) from exc
    return {
        "schemaVersion": "realytics-legal-term-alias-review-v1",
        **result,
        "appendOnly": True,
    }


def _repository(
    project_id: str,
    request: Request,
    *,
    permission: str,
    step_up_action: str | None = None,
    read_only: bool = False,
) -> tuple[PostgresLegalGovernanceRepository, str, str]:
    authorization = authorize_request(request, permission)
    if not authorization.get("authenticated"):
        raise HTTPException(
            status_code=401,
            detail={"error": "authentication_required"},
        )
    if step_up_action:
        enforce_high_risk_step_up(
            authorization,
            action=step_up_action,
        )
    if storage_backend() != "postgres" or not postgres_configured():
        raise HTTPException(
            status_code=503,
            detail={
                "error": (
                    "normalized_legal_store_unavailable"
                    if read_only
                    else "normalized_legal_store_required"
                ),
                "message": "프로젝트 법률 기록에 필요한 서버 데이터베이스가 연결되어 있지 않습니다.",
            },
        )
    project = get_project_record(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    enforce_project_tenant_scope(request, project)
    user = authorization.get("user") or {}
    metadata = (
        project.get("_meta")
        if isinstance(project.get("_meta"), dict)
        else {}
    )
    tenant_id = str(
        user.get("tenantId")
        or metadata.get("tenantId")
        or metadata.get("tenant_id")
        or ""
    ).strip()
    actor = str(user.get("userId") or "").strip()
    role = str(
        authorization.get("role")
        or user.get("role")
        or ""
    ).strip().casefold()
    if (
        not tenant_id
        or not actor
        or not is_explicit_human_principal(authorization)
    ):
        raise HTTPException(
            status_code=403,
            detail={"error": "tenant_bound_human_principal_required"},
        )
    if read_only:
        scope = current_project_scope()
        if scope is None or scope.tenant_id != tenant_id:
            raise HTTPException(
                status_code=403,
                detail={"error": "tenant_bound_human_principal_required"},
            )
        transaction_factory = scope.transaction_factory or api_tenant_transaction
        return (
            PostgresLegalGovernanceRepository(
                tenant_id=tenant_id,
                project_id=project_id,
                connection_factory=lambda: transaction_factory(tenant_id),
            ),
            actor,
            role,
        )
    if not legal_review_database_configured():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "normalized_legal_store_required",
                "message": "법률 기록 저장과 승인에 필요한 전용 서버 연결이 준비되지 않았습니다.",
            },
        )
    return (
        PostgresLegalGovernanceRepository(
            tenant_id=tenant_id,
            project_id=project_id,
        ),
        actor,
        role,
    )


def _created(
    key: str,
    writer: Any,
) -> dict[str, Any]:
    try:
        value = writer()
    except LegalReviewDatabaseConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "normalized_legal_store_unavailable",
                "message": "법률 기록 저장에 필요한 전용 서버 역할을 확인하지 못했습니다.",
            },
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={"error": "legal_counsel_approver_required"},
        ) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "legal_governance_record_not_found"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "legal_governance_contract_rejected",
                "message": str(exc),
            },
        ) from exc
    return {
        "schemaVersion": "realytics-legal-governance-write-v1",
        key: value,
        "appendOnly": True,
    }


__all__ = ["router"]
