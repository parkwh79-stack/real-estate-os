from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from agent_platform.coding import CodingPodLifecycleError, CodingPodService
from agent_platform.brains import BrainRoutingPolicyError, BrainRoutingPolicyService
from agent_platform.contracts import (
    BrainProviderHealth,
    BrainRoutingEventRecord,
    BrainRoutingPolicyRecord,
    CodingPodJob,
    LearningCandidate,
    LearningEvaluation,
    LearningPromotionDecision,
    RuntimeOutboxEvent,
    ToolCallRecord,
    ToolRequest,
)
from agent_platform.learning import LearningLifecycleError, LearningLifecycleService
from agent_platform.operational import AgentPlatformWorker, OperationalRepository
from agent_platform.tools import ToolExecutionDenied, ToolExecutor, build_default_tool_registry
from agent_platform.runtime import DurableAgentRuntime, StorageAgentRunRepository
from agent_platform.bootstrap import default_agent_registry
from core.authz import authorize_request


router = APIRouter(prefix="/agent-platform", tags=["Agent Platform"])
repository = OperationalRepository()
learning_service = LearningLifecycleService(repository)
coding_service = CodingPodService(repository)
tool_registry = build_default_tool_registry()
tool_executor = ToolExecutor(registry=tool_registry, repository=repository)
brain_policy_service = BrainRoutingPolicyService(repository)


class CodingWorkerCommand(BaseModel):
    worker_id: str = Field(..., alias="workerId", min_length=1)

    model_config = {"populate_by_name": True}


class CodingCompleteCommand(CodingWorkerCommand):
    artifact_ids: list[str] = Field(..., alias="artifactIds", min_length=1)


class CodingFailCommand(CodingWorkerCommand):
    error: str = Field(..., min_length=1, max_length=2000)


class CodingCancelCommand(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)


class OutboxRequeueCommand(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)


@router.get("/runtime/outbox")
def list_outbox(
    request: Request,
    status: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
):
    identity = authorize_request(request, "audit:read")
    filters: dict[str, Any] = {}
    if status:
        filters["status"] = status
    tenant_id = _tenant_id(identity)
    if tenant_id:
        filters["tenant_id"] = tenant_id
    return [item.model_dump(by_alias=True, mode="json") for item in repository.list(
        RuntimeOutboxEvent, filters=filters, limit=limit,
    )]


@router.post("/runtime/recover-expired")
def recover_expired_agent_runs(request: Request):
    authorize_request(request, "agent:operate")
    runtime = DurableAgentRuntime(
        registry=default_agent_registry(),
        repository=StorageAgentRunRepository(),
        worker_id=f"recovery-api:{uuid4()}",
    )
    recovered = runtime.recover_expired_runs()
    return {
        "recoveredCount": len(recovered),
        "runs": [item.model_dump(by_alias=True, mode="json") for item in recovered],
    }


@router.post("/runtime/outbox/dispatch")
def dispatch_runtime_outbox(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
):
    authorize_request(request, "agent:operate")
    return AgentPlatformWorker(worker_id=f"outbox-api:{uuid4()}").run_once(limit=limit)


@router.post("/runtime/outbox/{event_id}/requeue")
def requeue_runtime_outbox(
    event_id: str,
    command: OutboxRequeueCommand,
    request: Request,
):
    identity = authorize_request(request, "agent:operate")
    requested_by = str((identity.get("user") or {}).get("userId") or "unknown")
    try:
        event = repository.requeue_outbox(
            event_id,
            requested_by=requested_by,
            reason=command.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return event.model_dump(by_alias=True, mode="json")


@router.get("/brains/health")
def list_brain_health(request: Request, limit: int = Query(100, ge=1, le=1000)):
    identity = authorize_request(request, "audit:read")
    filters = {"tenant_id": _tenant_id(identity)} if _tenant_id(identity) else {}
    return [item.model_dump(by_alias=True, mode="json") for item in repository.list(
        BrainProviderHealth, filters=filters, limit=limit,
    )]


@router.get("/brains/routing-events")
def list_brain_routing_events(request: Request, limit: int = Query(100, ge=1, le=1000)):
    identity = authorize_request(request, "audit:read")
    filters = {"tenant_id": _tenant_id(identity)} if _tenant_id(identity) else {}
    return [item.model_dump(by_alias=True, mode="json") for item in repository.list(
        BrainRoutingEventRecord, filters=filters, limit=limit,
    )]


@router.get("/brains/policies")
def list_brain_policies(request: Request):
    identity = authorize_request(request, "audit:read")
    filters = {"tenant_id": _tenant_id(identity)} if _tenant_id(identity) else {}
    return [item.model_dump(by_alias=True, mode="json") for item in repository.list(
        BrainRoutingPolicyRecord, filters=filters, limit=1000,
    )]


@router.post("/brains/policies")
def save_brain_policy(policy: BrainRoutingPolicyRecord, request: Request):
    identity = authorize_request(request, "brain:manage")
    _enforce_tenant(policy.tenant_id, identity)
    try:
        saved = brain_policy_service.save(policy)
    except BrainRoutingPolicyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return saved.model_dump(by_alias=True, mode="json")


@router.get("/tools")
def list_tools(request: Request):
    authorize_request(request, "tool:read")
    return {
        "tools": [item.model_dump(by_alias=True, mode="json") for item in tool_registry.list_active()],
        "executionPolicy": "Only registered, contract-validated tools can be executed.",
    }


@router.get("/tool-calls")
def list_tool_calls(request: Request, limit: int = Query(100, ge=1, le=1000)):
    identity = authorize_request(request, "audit:read")
    filters = {"tenant_id": _tenant_id(identity)} if _tenant_id(identity) else {}
    return [item.model_dump(by_alias=True, mode="json") for item in repository.list(
        ToolCallRecord, filters=filters, limit=limit,
    )]


@router.post("/tools/execute")
def execute_tool(tool_request: ToolRequest, request: Request):
    identity = authorize_request(request, "tool:execute")
    _enforce_tenant(tool_request.tenant_id, identity)
    user_id = str((identity.get("user") or {}).get("userId") or "unknown")
    role = str(identity.get("role") or "")
    controlled = tool_request.model_copy(update={
        "user_id": user_id,
        "actor_roles": sorted(set([*tool_request.actor_roles, role]) - {""}),
    })
    try:
        return tool_executor.execute(controlled).model_dump(by_alias=True, mode="json")
    except ToolExecutionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/learning/candidates")
def list_learning_candidates(request: Request, status: str | None = None):
    identity = authorize_request(request, "learning:read")
    filters: dict[str, Any] = {}
    if _tenant_id(identity):
        filters["tenant_id"] = _tenant_id(identity)
    if status:
        filters["status"] = status
    return [item.model_dump(by_alias=True, mode="json") for item in repository.list(
        LearningCandidate, filters=filters, limit=1000,
    )]


@router.post("/learning/candidates")
def create_learning_candidate(candidate: LearningCandidate, request: Request):
    identity = authorize_request(request, "learning:review")
    _enforce_tenant(candidate.tenant_id, identity)
    return learning_service.create_candidate(candidate).model_dump(by_alias=True, mode="json")


@router.post("/learning/candidates/{candidate_id}/submit")
def submit_learning_candidate(candidate_id: str, request: Request):
    authorize_request(request, "learning:review")
    return learning_service.submit(candidate_id).model_dump(by_alias=True, mode="json")


@router.post("/learning/evaluations")
def record_learning_evaluation(evaluation: LearningEvaluation, request: Request):
    identity = authorize_request(request, "learning:review")
    _enforce_tenant(evaluation.tenant_id, identity)
    try:
        return learning_service.record_evaluation(evaluation).model_dump(by_alias=True, mode="json")
    except LearningLifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/learning/decisions")
def decide_learning_candidate(decision: LearningPromotionDecision, request: Request):
    identity = authorize_request(request, "learning:promote")
    _enforce_tenant(decision.tenant_id, identity)
    user_id = str((identity.get("user") or {}).get("userId") or "unknown")
    controlled = decision.model_copy(update={"approved_by": user_id, "approved_at": datetime.now(timezone.utc)})
    try:
        return learning_service.decide(controlled).model_dump(by_alias=True, mode="json")
    except LearningLifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/coding/jobs")
def list_coding_jobs(request: Request, status: str | None = None):
    identity = authorize_request(request, "coding:operate")
    filters: dict[str, Any] = {}
    if _tenant_id(identity):
        filters["tenant_id"] = _tenant_id(identity)
    if status:
        filters["status"] = status
    return [item.model_dump(by_alias=True, mode="json") for item in repository.list(
        CodingPodJob, filters=filters, limit=1000,
    )]


@router.post("/coding/jobs")
def submit_coding_job(job: CodingPodJob, request: Request):
    identity = authorize_request(request, "coding:request")
    _enforce_tenant(job.tenant_id, identity)
    user_id = str((identity.get("user") or {}).get("userId") or "unknown")
    controlled = job.model_copy(update={"requested_by": user_id})
    return coding_service.submit(controlled).model_dump(by_alias=True, mode="json")


@router.post("/coding/jobs/claim")
def claim_coding_job(request: Request, worker_id: str, lease_seconds: int = 120):
    authorize_request(request, "coding:operate")
    job = coding_service.claim(worker_id=worker_id, lease_seconds=lease_seconds)
    return job.model_dump(by_alias=True, mode="json") if job else None


@router.post("/coding/jobs/{job_id}/heartbeat")
def heartbeat_coding_job(job_id: str, request: Request, worker_id: str, lease_seconds: int = 120):
    authorize_request(request, "coding:operate")
    return coding_service.heartbeat(
        job_id, worker_id=worker_id, lease_seconds=lease_seconds,
    ).model_dump(by_alias=True, mode="json")


@router.post("/coding/jobs/{job_id}/start")
def start_coding_job(job_id: str, command: CodingWorkerCommand, request: Request):
    authorize_request(request, "coding:operate")
    try:
        job = coding_service.start(job_id, worker_id=command.worker_id)
    except (KeyError, CodingPodLifecycleError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.model_dump(by_alias=True, mode="json")


@router.post("/coding/jobs/{job_id}/complete")
def complete_coding_job(job_id: str, command: CodingCompleteCommand, request: Request):
    authorize_request(request, "coding:operate")
    try:
        job = coding_service.complete(
            job_id,
            worker_id=command.worker_id,
            artifact_ids=command.artifact_ids,
        )
    except (KeyError, CodingPodLifecycleError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.model_dump(by_alias=True, mode="json")


@router.post("/coding/jobs/{job_id}/fail")
def fail_coding_job(job_id: str, command: CodingFailCommand, request: Request):
    authorize_request(request, "coding:operate")
    try:
        job = coding_service.fail(job_id, worker_id=command.worker_id, error=command.error)
    except (KeyError, CodingPodLifecycleError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.model_dump(by_alias=True, mode="json")


@router.post("/coding/jobs/{job_id}/cancel")
def cancel_coding_job(job_id: str, command: CodingCancelCommand, request: Request):
    identity = authorize_request(request, "coding:request")
    requested_by = str((identity.get("user") or {}).get("userId") or "unknown")
    existing = repository.get(CodingPodJob, job_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=job_id)
    _enforce_tenant(existing.tenant_id, identity)
    try:
        job = coding_service.cancel(
            job_id,
            requested_by=requested_by,
            reason=command.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CodingPodLifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.model_dump(by_alias=True, mode="json")


def _tenant_id(identity: dict[str, Any]) -> str | None:
    value = (identity.get("user") or {}).get("tenantId")
    return str(value) if value else None


def _enforce_tenant(tenant_id: str, identity: dict[str, Any]) -> None:
    expected = _tenant_id(identity)
    if expected and expected != tenant_id:
        raise HTTPException(status_code=403, detail="TENANT_SCOPE_MISMATCH")
