from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_platform.brains import BrainRoutingPolicyError, BrainRoutingPolicyService
from agent_platform.coding import CodingPodService
from agent_platform.coding import RemoteIsolatedCodingExecutor
from agent_platform.contracts import (
    BrainRoutingPolicyRecord,
    CodingPodJob,
    CodingPodJobStatus,
    DataClassification,
    LearningCandidate,
    LearningEvaluation,
    LearningPromotionDecision,
    OutboxEventStatus,
    RuntimeOutboxEvent,
    ToolRequest,
)
from agent_platform.learning import LearningLifecycleService
from agent_platform.operational import AgentPlatformWorker, OperationalRepository
from agent_platform.operational.handlers import build_default_outbox_handlers
from agent_platform.operational.outbox import RuntimeOutboxDispatcher
from agent_platform.tools import ToolExecutor, build_default_tool_registry
from agents.ai_connection import AIConnection, AIExtractionResult
from agents.orchestration_agent import OrchestrationAgent
from use_cases.glossary import capture_unmapped_term_surface_candidates_use_case
from storage import (
    list_agent_run_records,
    list_deal_state_records,
    upsert_calculation_run_record,
)


@pytest.fixture(autouse=True)
def isolated_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "agent-platform.sqlite3"))


def test_calculation_run_and_outbox_are_persisted_and_reprocessed():
    run = upsert_calculation_run_record("project-ops", {
        "tenantId": "tenant-1",
        "calculationRunId": "calc-1",
        "engineKey": "cash_flow",
        "engineVersion": "cash-flow-v2",
        "outputMetrics": [{
            "key": "cashflow.max_minus",
            "value": -100,
            "sourceLocator": {"calculationRunId": "calc-1", "outputPath": "maxMinus"},
        }],
    })
    assert run["calculationRunId"] == "calc-1"

    repository = OperationalRepository()
    events = repository.list(RuntimeOutboxEvent, filters={"event_type": "calculation_run_upserted"})
    assert len(events) == 1
    assert events[0].status == OutboxEventStatus.PENDING

    result = AgentPlatformWorker(worker_id="worker-1").run_once()
    assert result == {"claimed": 1, "published": 1, "failed": 0}
    published = repository.get(RuntimeOutboxEvent, events[0].event_id)
    assert published and published.status == OutboxEventStatus.PUBLISHED
    metrics = list_deal_state_records("project-ops", record_type="metric")
    assert any(item.get("key") == "cashflow.max_minus" for item in metrics)


def test_default_worker_looks_up_parser_snapshot_by_identity(monkeypatch):
    from db.repositories import parser_v2_repository as parser_repository

    calls = []

    class _ParserRepositoryStub:
        def get_approved_snapshot(self, tenant_id, project_id, snapshot_id):
            calls.append((tenant_id, project_id, snapshot_id))
            return {
                "eventKind": "created",
                "snapshotId": "snapshot-1",
                "selectionIds": ["selection-1"],
            }

        def list_records(self, *_args, **_kwargs):
            raise AssertionError("snapshot verification must not use a bounded list")

    monkeypatch.setattr(
        parser_repository,
        "ParserV2PostgresRepository",
        _ParserRepositoryStub,
    )
    handler = build_default_outbox_handlers()[
        "parser.approved_snapshot.created"
    ]

    handler(
        {
            "tenantId": "tenant-1",
            "projectId": "project-1",
            "snapshotId": "snapshot-1",
            "selectionIds": ["selection-1"],
        }
    )

    assert calls == [("tenant-1", "project-1", "snapshot-1")]


def test_parser_snapshot_handler_rejects_mismatched_selection_set(monkeypatch):
    from db.repositories import parser_v2_repository as parser_repository

    class _ParserRepositoryStub:
        def get_approved_snapshot(self, *_args, **_kwargs):
            return {
                "eventKind": "created",
                "snapshotId": "snapshot-1",
                "selectionIds": ["selection-authoritative"],
            }

    monkeypatch.setattr(
        parser_repository,
        "ParserV2PostgresRepository",
        _ParserRepositoryStub,
    )
    handler = build_default_outbox_handlers()[
        "parser.approved_snapshot.created"
    ]

    with pytest.raises(
        RuntimeError,
        match="OUTBOX_PARSER_SNAPSHOT_SELECTIONS_MISMATCH",
    ):
        handler(
            {
                "tenantId": "tenant-1",
                "projectId": "project-1",
                "snapshotId": "snapshot-1",
                "selectionIds": ["selection-stale"],
            }
        )


def test_coding_pod_claim_is_exclusive_and_recovers_expired_worker():
    now = datetime.now(timezone.utc)
    repository = OperationalRepository()
    service = CodingPodService(repository)
    submitted = service.submit(CodingPodJob(
        job_id="coding-job-1",
        tenant_id="tenant-1",
        requested_by="user-1",
        workspace_ref="repo-1",
        branch_name="codex/isolated-job",
        requested_change="Run isolated coding validation",
        allowed_paths=["services/api-server"],
        idempotency_key="coding-idem-1",
        created_at=now,
        updated_at=now,
    ))
    assert submitted.status == CodingPodJobStatus.QUEUED

    first = service.claim(worker_id="worker-a", lease_seconds=30)
    assert first and first.lease_owner == "worker-a"
    assert first.attempt_count == 1
    assert service.claim(worker_id="worker-b", lease_seconds=30) is None

    repository.save(first.model_copy(update={
        "status": CodingPodJobStatus.RUNNING,
        "lease_expires_at": now - timedelta(seconds=1),
    }))
    recovered = service.claim(worker_id="worker-b", lease_seconds=30)
    assert recovered and recovered.lease_owner == "worker-b"
    assert recovered.attempt_count == 2
    started = service.start(recovered.job_id, worker_id="worker-b")
    completed = service.complete(
        started.job_id,
        worker_id="worker-b",
        artifact_ids=["artifact:test-result"],
    )
    assert completed.status == CodingPodJobStatus.SUCCEEDED


def test_coding_pod_can_be_cancelled_and_worker_cannot_complete_it():
    now = datetime.now(timezone.utc)
    repository = OperationalRepository()
    service = CodingPodService(repository)
    service.submit(CodingPodJob(
        job_id="coding-job-cancel",
        tenant_id="tenant-1",
        requested_by="user-1",
        workspace_ref="repo-1",
        branch_name="codex/cancel-job",
        requested_change="Cancel this isolated job",
        allowed_paths=["services/api-server"],
        idempotency_key="coding-idem-cancel",
        created_at=now,
        updated_at=now,
    ))
    claimed = service.claim(worker_id="worker-cancel", lease_seconds=30)
    assert claimed is not None
    cancelled = service.cancel(
        claimed.job_id,
        requested_by="reviewer-1",
        reason="Request was superseded",
    )
    assert cancelled.status == CodingPodJobStatus.CANCELLED
    with pytest.raises(Exception, match="CODING_POD_LEASE_NOT_OWNED"):
        service.complete(
            claimed.job_id,
            worker_id="worker-cancel",
            artifact_ids=["artifact:late"],
        )


def test_remote_coding_executor_sends_only_the_isolated_job_contract(monkeypatch):
    now = datetime.now(timezone.utc)
    captured: dict = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"status":"completed","artifactIds":["artifact:coding-1"]}'

    def fake_urlopen(request, timeout):
        captured["payload"] = request.data.decode("utf-8")
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    executor = RemoteIsolatedCodingExecutor(
        endpoint="http://localhost:9010",
        token="ephemeral-worker-token",
        heartbeat_seconds=30,
    )
    heartbeats: list[bool] = []
    artifacts = executor.execute(
        CodingPodJob(
            job_id="coding-remote-1",
            tenant_id="tenant-1",
            requested_by="user-1",
            workspace_ref="repo-1",
            branch_name="codex/remote-job",
            requested_change="Run tests",
            allowed_paths=["services/api-server"],
            idempotency_key="coding-remote-idem",
            created_at=now,
            updated_at=now,
        ),
        heartbeat=lambda: heartbeats.append(True),
    )
    assert artifacts == ["artifact:coding-1"]
    assert heartbeats
    assert "ephemeral-worker-token" not in captured["payload"]
    assert '"credentialsIncluded": false' in captured["payload"]


def test_default_tool_registry_reads_real_ledger_and_audits_idempotently():
    upsert_calculation_run_record("project-tool", {
        "tenantId": "tenant-1",
        "calculationRunId": "calc-tool-1",
        "engineKey": "waterfall",
        "engineVersion": "waterfall-v1",
    })
    repository = OperationalRepository()
    executor = ToolExecutor(
        registry=build_default_tool_registry(),
        repository=repository,
    )
    now = datetime.now(timezone.utc)
    request = ToolRequest(
        tool_call_id="tool-call-1",
        run_id="run-tool-1",
        tenant_id="tenant-1",
        user_id="user-1",
        project_id="project-tool",
        tool_id="calculation_runs.read",
        tool_version="1.0.0",
        data_classification=DataClassification.CONFIDENTIAL,
        actor_roles=["reviewer"],
        payload={"engineKey": "waterfall"},
        idempotency_key="tool-idem-1",
        requested_at=now,
    )
    first = executor.execute(request)
    second = executor.execute(request.model_copy(update={"tool_call_id": "tool-call-retry"}))
    assert first.output and first.output["records"][0]["calculationRunId"] == "calc-tool-1"
    assert second.tool_call_id == first.tool_call_id


def test_outbox_failure_retries_and_then_dead_letters():
    repository = OperationalRepository()
    now = datetime.now(timezone.utc)
    event = repository.enqueue_outbox(RuntimeOutboxEvent(
        event_id="outbox-failure-1",
        tenant_id="tenant-1",
        aggregate_type="test",
        aggregate_id="aggregate-1",
        event_type="always_fails",
        idempotency_key="outbox-failure-idem-1",
        max_attempts=2,
        available_at=now,
        created_at=now,
        updated_at=now,
    ))
    dispatcher = RuntimeOutboxDispatcher(
        repository=repository,
        handlers={"always_fails": lambda _payload: (_ for _ in ()).throw(RuntimeError("boom"))},
    )
    first = dispatcher.publish_batch(worker_id="outbox-worker")
    assert first == {"claimed": 1, "published": 0, "failed": 1}
    retrying = repository.get(RuntimeOutboxEvent, event.event_id)
    assert retrying and retrying.status == OutboxEventStatus.RETRYING
    repository.save(retrying.model_copy(update={"available_at": now - timedelta(seconds=1)}))
    second = dispatcher.publish_batch(worker_id="outbox-worker")
    assert second == {"claimed": 1, "published": 0, "failed": 1}
    dead = repository.get(RuntimeOutboxEvent, event.event_id)
    assert dead and dead.status == OutboxEventStatus.DEAD_LETTER


def test_ai_connection_falls_back_after_a_real_provider_failure(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    connection = AIConnection()
    attempted: list[str] = []

    def fake_extract(_prompt, _system, context):
        provider = str(context.get("provider"))
        attempted.append(provider)
        context["provider_call_attempted"] = True
        if provider == "openai":
            return AIExtractionResult(data=None, meta={}, error="primary failed")
        return AIExtractionResult(data={"provider": provider}, meta={})

    monkeypatch.setattr(connection, "_extract_for_context", fake_extract)
    result = connection.extract_json(
        "prompt",
        system="system",
        ai_context={
            "provider": "openai",
            "tenant_id": "tenant-fallback",
            "user_id": "user-fallback",
            "project_id": "project-fallback",
            "max_cost_usd": 10,
        },
    )
    assert attempted[0] == "openai"
    assert len(attempted) == 2
    assert result.data and result.data["provider"] != "openai"
    assert result.meta["fallbackFromProvider"] == "openai"


def test_selected_workflow_domain_agents_use_durable_runtime():
    run = OrchestrationAgent().run_workflow(
        "project-domain-runtime",
        {"feasibility_input": {"total_revenue": 1000, "total_cost": 800}},
        "feasibility",
        {"tenantId": "tenant-1", "userId": "user-1"},
    )
    executions = run.get("agentRuntimeExecutions") or []
    assert any(item.get("agentId") == "developmentfeasibilityagent" for item in executions)
    persisted = list_agent_run_records(project_id="project-domain-runtime", limit=100)
    assert any(item.get("agentId") == "developmentfeasibilityagent" for item in persisted)


def test_learning_candidate_requires_evaluation_canary_and_supports_rollback():
    repository = OperationalRepository()
    service = LearningLifecycleService(repository)
    now = datetime.now(timezone.utc)
    candidate = service.create_candidate(LearningCandidate(
        candidate_id="candidate-1",
        tenant_id="tenant-1",
        project_id="project-1",
        candidate_type="workbook_mapping",
        target_key="template-a",
        proposed_change={"sheet": "사업수지", "cell": "F22"},
        evidence_ids=["archive:workbook-1"],
        created_by="user-1",
        created_at=now,
        updated_at=now,
    ))
    service.submit(candidate.candidate_id)
    evaluated = service.record_evaluation(LearningEvaluation(
        evaluation_id="evaluation-1",
        candidate_id=candidate.candidate_id,
        tenant_id="tenant-1",
        evaluation_type="regression_fixture",
        passed=True,
        findings=[],
        evaluated_by="qa-worker",
        evaluated_at=now,
    ))
    assert evaluated.status.value == "evaluated"

    canary = service.decide(_decision("decision-1", "start_canary", now))
    promoted = service.decide(_decision("decision-2", "promote", now))
    rolled_back = service.decide(_decision("decision-3", "rollback", now))
    assert canary.status.value == "canary"
    assert promoted.status.value == "promoted"
    assert rolled_back.status.value == "rolled_back"


def test_unmapped_glossary_surface_enters_governed_learning_lifecycle():
    result = capture_unmapped_term_surface_candidates_use_case(
        [{
            "rawText": "pilot_unique_finance_term_zz981",
            "sourceField": "pf_structure.experimentalField",
            "sourceLocator": {
                "sourceType": "document",
                "evidenceId": "evidence:glossary-1",
                "documentId": "document-1",
                "page": 9,
            },
        }],
        project_id="project-learning-glossary",
        run_id="run-learning-glossary",
        tenant_id="tenant-1",
        created_by="reviewer-1",
    )
    assert result["learningCandidateCount"] == 1
    candidate = OperationalRepository().get(
        LearningCandidate,
        result["learningCandidateIds"][0],
    )
    assert candidate is not None
    assert candidate.candidate_type == "glossary_term_mapping"
    assert candidate.evidence_ids == ["evidence:glossary-1"]


def test_brain_routing_policy_versions_are_monotonic_and_supersede_active_policy():
    repository = OperationalRepository()
    service = BrainRoutingPolicyService(repository)
    now = datetime.now(timezone.utc)
    first = service.save(_policy("policy-1", 1, now))
    assert first.active is True
    with pytest.raises(BrainRoutingPolicyError, match="VERSION_EXPECTED:2"):
        service.save(_policy("policy-bad", 3, now))
    second = service.save(_policy("policy-2", 2, now, supersedes="policy-1"))
    assert second.active is True
    assert repository.get(BrainRoutingPolicyRecord, "policy-1").active is False


def _decision(decision_id: str, action: str, now: datetime) -> LearningPromotionDecision:
    return LearningPromotionDecision(
        decision_id=decision_id,
        candidate_id="candidate-1",
        tenant_id="tenant-1",
        action=action,
        environment="pilot",
        reason=f"test {action}",
        approved_by="reviewer-1",
        approved_at=now,
    )


def _policy(
    policy_id: str,
    version: int,
    now: datetime,
    *,
    supersedes: str | None = None,
) -> BrainRoutingPolicyRecord:
    return BrainRoutingPolicyRecord(
        policy_id=policy_id,
        tenant_id="tenant-1",
        version=version,
        active=True,
        allowed_providers=["gemini", "openai"],
        max_cost_usd=1,
        created_by="admin-1",
        created_at=now,
        supersedes_policy_id=supersedes,
    )
