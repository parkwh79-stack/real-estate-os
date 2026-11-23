from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from copy import deepcopy
import pytest
from db import postgres
from db.repositories.request_project_repository import project_request_scope
from db.repositories import runtime_record_repository as runtime


def test_worker_claim_and_update_use_worker_pool_and_enforce_lease(monkeypatch):
    calls=[]
    payload={'jobId':'j','tenantId':'tenant-a','projectId':'p','attemptCount':0,'status':'queued'}
    class Connection:
        def execute(self, sql, params):
            calls.append((sql, params))
            self.claim='SELECT id, payload_json' in sql
            return self
        def fetchone(self):
            return ('j', deepcopy(payload), 0) if self.claim else ('tenant-a',)
    @contextmanager
    def worker_transaction(tenant):
        assert tenant == 'tenant-a'
        yield Connection()
    monkeypatch.setattr(postgres, 'connect', lambda *_: pytest.fail('admin DSN used'))
    monkeypatch.setattr(runtime, 'api_tenant_transaction', lambda *_: pytest.fail('API pool used by worker'))
    now=datetime.now(timezone.utc)
    with project_request_scope(lambda: SimpleNamespace(tenant_id='tenant-a'), transaction_factory=worker_transaction, worker_id='worker-a'):
        job=postgres.claim_async_job_record(worker_id='worker-a',now=now,lease_expires_at=now+timedelta(minutes=1))
        assert job['attemptCount']==1 and job['leaseOwner']=='worker-a'
        postgres.upsert_typed_jsonb_record('async_jobs', {'id':'j','project_id':'p','attempt_count':1,'status':'completed'}, {**job,'status':'completed'})
    assert 'FOR UPDATE SKIP LOCKED' in calls[0][0]
    assert calls[0][1][0]=='tenant-a'
    assert 'async_jobs.lease_owner = %s AND async_jobs.attempt_count = %s' in calls[-1][0]
    assert calls[-1][1][-2:]==['worker-a',1]


def test_worker_requires_explicit_tenant_before_database_access(monkeypatch):
    from use_cases.async_jobs import PFReviewJobWorker
    import storage
    monkeypatch.setattr(storage, 'storage_backend', lambda:'postgres')
    monkeypatch.setattr(postgres, 'connect', lambda *_: pytest.fail('admin DSN used'))
    with pytest.raises(RuntimeError, match='tenant_id'):
        PFReviewJobWorker(lambda job: None).run_once()


def test_agent_run_and_outbox_share_worker_transaction_and_rollback(monkeypatch):
    outcomes=[]
    class Connection:
        def execute(self, sql, params):
            if 'runtime_outbox_events' in sql:
                raise RuntimeError('simulated outbox failure')
            return self
        def fetchone(self):
            return ('tenant-a',)
    @contextmanager
    def transaction(tenant):
        try:
            yield Connection()
        except Exception:
            outcomes.append('rollback')
            raise
        else:
            outcomes.append('commit')
    monkeypatch.setattr(postgres,'connect',lambda *_: pytest.fail('admin DSN used'))
    with project_request_scope(lambda:SimpleNamespace(tenant_id='tenant-a'),transaction_factory=transaction,worker_id='worker-a'):
        with pytest.raises(RuntimeError,match='simulated'):
            postgres.update_agent_run_with_outbox({'run_id':'r','state_version':2}, {'runId':'r'},
                expected_state_version=1, outbox_values={'event_id':'e'},outbox_payload={'eventId':'e'})
    assert outcomes==['rollback']


def test_lease_loss_rolls_back_and_cannot_complete_another_workers_job():
    class Connection:
        def execute(self,*_): return self
        def fetchone(self): return None
    @contextmanager
    def transaction(_): yield Connection()
    with project_request_scope(lambda:SimpleNamespace(tenant_id='tenant-a'), transaction_factory=transaction,worker_id='expired-worker'):
        with pytest.raises(runtime.WorkerLeaseLost,match='no longer owned'):
            postgres.upsert_typed_jsonb_record('async_jobs',{'id':'j','attempt_count':1}, {'status':'completed'})


def test_worker_entrypoint_opens_and_closes_only_its_pool(monkeypatch):
    from scripts import run_pf_review_worker as entrypoint
    from db import postgres_runtime_pool as pools
    import storage
    calls=[]
    monkeypatch.setattr(storage,'storage_backend',lambda:'postgres')
    monkeypatch.setenv('REALYTICS_PF_WORKER_TENANT_ID','tenant-a')
    monkeypatch.setenv('REALYTICS_PF_WORKER_DATABASE_URL','postgresql://worker@test.invalid/db')
    monkeypatch.delenv(pools.RUNTIME_POOL_ROLES_ENV,raising=False)
    class Worker:
        def __init__(self,factory,tenant_id): assert tenant_id=='tenant-a'
        def run_forever(self,**_):
            calls.append('run')
            raise RuntimeError('stop-test')
    monkeypatch.setattr(entrypoint,'PFReviewJobWorker',Worker)
    def open_pool():
        import os
        assert os.environ[pools.RUNTIME_POOL_ROLES_ENV]=='pf_worker'
        calls.append('open')
    monkeypatch.setattr(pools,'open_runtime_pools',open_pool)
    monkeypatch.setattr(pools,'close_runtime_pools',lambda:calls.append('close'))
    with pytest.raises(RuntimeError,match='stop-test'):
        entrypoint.main()
    assert calls==['open','run','close']


def test_worker_entrypoint_rejects_missing_dedicated_connection(monkeypatch):
    from scripts import run_pf_review_worker as entrypoint
    from db import postgres_runtime_pool as pools
    import storage
    monkeypatch.setattr(storage,'storage_backend',lambda:'postgres')
    monkeypatch.delenv('REALYTICS_PF_WORKER_DATABASE_URL',raising=False)
    monkeypatch.setattr(pools,'open_runtime_pools',lambda:pytest.fail('must validate before opening'))
    with pytest.raises(RuntimeError,match='dedicated'):
        entrypoint.main()
