import asyncio
from copy import deepcopy

import pytest
from starlette.concurrency import run_in_threadpool

from db.repositories.request_project_repository import current_project_scope, project_request_scope
from db.repositories.postgres_project_repository import ProjectVersionConflict


class Authority:
    def __init__(self, tenant):
        self.tenant_id = tenant
        self.row = {'id': 'p', 'project': {'id': 'p'}, '_meta': {'tenantId': tenant, 'stateVersion': 1}}
    def get(self, key):
        return deepcopy(self.row) if key == 'p' else None
    def update(self, key, data, expected_state_version=None):
        if expected_state_version != self.row['_meta']['stateVersion']:
            raise ProjectVersionConflict(project_id=key, expected_state_version=expected_state_version,
                                         actual_state_version=self.row['_meta']['stateVersion'])
        self.row = deepcopy(data)
        self.row['_meta']['stateVersion'] = expected_state_version + 1
        return deepcopy(self.row)


def test_request_scope_propagates_to_threadpool_and_isolates_concurrent_tenants():
    async def check(tenant):
        with project_request_scope(lambda: Authority(tenant)) as scope:
            scope['p']['working'] = tenant
            await asyncio.sleep(0)
            result = await run_in_threadpool(lambda: current_project_scope()['p'])
            assert result['working'] == tenant
            assert result['_meta']['tenantId'] == tenant
    async def run():
        await asyncio.gather(check('a'), check('b'))
    asyncio.run(run())
    assert current_project_scope() is None


def test_writes_use_observed_version_and_preserve_live_reference():
    authority = Authority('a')
    with project_request_scope(lambda: authority) as scope:
        original = scope['p']
        scope.apply_parsed_data('p', {**original, 'source_documents': [{'archiveId': 'doc-1'}]})
        assert original['source_documents'][0]['archiveId'] == 'doc-1'
        assert original['_meta']['stateVersion'] == 2
        authority.row['_meta']['stateVersion'] = 3
        with pytest.raises(ProjectVersionConflict):
            scope.persist('p')
    assert authority.row['_meta']['stateVersion'] == 3


def test_report_snapshot_and_orchestration_factories_reuse_verified_scope(monkeypatch):
    from routers import projects
    from fastapi import HTTPException
    monkeypatch.setattr(projects, 'storage_backend', lambda: 'postgres')
    with project_request_scope(lambda: Authority('a')):
        for factory in (projects.report_repository, projects.project_snapshot_repository, projects.orchestration_repository):
            assert factory().tenant_id == 'a'
            with pytest.raises(HTTPException) as error:
                factory(tenant_id='b')
            assert error.value.status_code == 403


def test_normalized_parser_uses_scoped_transaction_without_admin_dsn(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from db import postgres
    from db.repositories.parser_v2_repository import ParserV2PostgresRepository, ParserV2TenantScopeError
    calls=[]
    @contextmanager
    def transaction(tenant):
        calls.append(tenant)
        yield 'scoped-connection'
    monkeypatch.setattr(postgres, 'connect', lambda *_: pytest.fail('admin DSN used'))
    repo=ParserV2PostgresRepository()
    session=SimpleNamespace(validate_batch=lambda:calls.append('validated'))
    def bind(conn,tenant,project):
        assert conn=='scoped-connection' and project=='p'
        return session
    monkeypatch.setattr(repo,'bind',bind)
    with project_request_scope(lambda:Authority('a'),transaction_factory=transaction):
        with repo.transaction('a','p') as actual:
            assert actual is session
        with pytest.raises(ParserV2TenantScopeError):
            with repo.transaction('b','p'):
                pytest.fail('wrong tenant allowed')
    assert calls==['a','validated']
