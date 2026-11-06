"""Request-local project working copies backed exclusively by tenant authority.

ContextVars propagate into FastAPI's sync threadpool. The scope object belongs to
one HTTP request; no normalized record is cached in a process-global dictionary.
Writes are explicit and use the version observed by this request.
"""
from __future__ import annotations

from collections.abc import MutableMapping, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from typing import Any, Callable


_scope: ContextVar[RequestProjectRepository | None] = ContextVar("project_request_repository", default=None)


def current_project_scope() -> RequestProjectRepository | None:
    return _scope.get()


@contextmanager
def project_request_scope(factory: Callable[[], Any], *, transaction_factory=None, worker_id=None):
    scope = RequestProjectRepository(factory)
    scope.transaction_factory = transaction_factory
    scope.worker_id = worker_id
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        _scope.reset(token)


class RequestProjectRepository(MutableMapping):
    def __init__(self, factory: Callable[[], Any]):
        self._factory = factory
        self._authority = None
        self._records: dict[str, dict[str, Any]] = {}
        self._versions: dict[str, int] = {}
        self.transaction_factory = None
        self.worker_id = None

    @property
    def authority(self):
        if self._authority is None:
            self._authority = self._factory()
        return self._authority

    @property
    def tenant_id(self) -> str:
        return self.authority.tenant_id

    def _adopt(self, record):
        if record is None:
            return None
        record = deepcopy(record)
        key = str(record.get("id") or record.get("project", {}).get("id"))
        metadata = record.get("_meta") or {}
        if metadata.get("tenantId") != self.tenant_id:
            raise ValueError("Project tenant does not match request authority")
        self._versions[key] = int(metadata["stateVersion"])
        if key in self._records:
            self._records[key].clear()
            self._records[key].update(record)
        else:
            self._records[key] = record
        return self._records[key]

    def __getitem__(self, key):
        key = str(key)
        if key not in self._records:
            record = self.authority.get(key)
            if record is None:
                raise KeyError(key)
            self._adopt(record)
        return self._records[key]

    def __setitem__(self, key, value):
        # Assignment changes only a loaded working copy; persistence stays explicit.
        self[str(key)]
        self._records[str(key)] = deepcopy(value)

    def __delitem__(self, key):
        self.delete(str(key))

    def __iter__(self) -> Iterator[str]:
        self.list()
        return iter(self._records)

    def __len__(self):
        self.list()
        return len(self._records)

    def list(self):
        rows = self.authority.list()
        return [self._records.get(str(record.get('id'))) or self._adopt(record) for record in rows]

    def exists(self, project_id):
        return self.get(project_id) is not None

    def create(self, data, **kwargs):
        return self._adopt(self.authority.create(data, **kwargs))

    def update(self, project_id, data, *, expected_state_version=None):
        self[str(project_id)]
        expected = self._versions[str(project_id)] if expected_state_version is None else expected_state_version
        return self._adopt(self.authority.update(project_id, data, expected_state_version=expected))

    def delete(self, project_id, *, expected_state_version=None):
        self[str(project_id)]
        expected = self._versions[str(project_id)] if expected_state_version is None else expected_state_version
        result = self.authority.delete(project_id, expected_state_version=expected)
        self._records.pop(str(project_id), None)
        self._versions.pop(str(project_id), None)
        return result

    def persist(self, project_id):
        return self.update(project_id, self[str(project_id)])

    def apply_parsed_data(self, project_id, parsed_data):
        # The authority deep-merges business data and strips caller-owned scope.
        return self.update(project_id, parsed_data)
