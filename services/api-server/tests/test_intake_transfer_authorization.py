"""Exercise upload transport boundaries with real identity and permission gates.

Only project lookup and object storage are injected. The minimal app deliberately
omits main's tenant middleware so every transfer endpoint must enforce its own
authorization before reading or changing staged bytes.
"""
from hashlib import sha256
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from db.object_storage import LocalObjectStorage
from routers import projects
from storage import create_user_session, revoke_user_session, upsert_user
from use_cases.intake_transfers import IntakeTransferStore


CONTENT = b"private project source document"
PROJECT_ID = "p"
TENANT_ID = "t"
OPERATIONS = ("create", "part", "classify", "process", "cancel")


def _metadata(**extra):
    return {
        "filename": "private-source.pdf", "contentType": "application/pdf",
        "sizeBytes": len(CONTENT), "sha256": sha256(CONTENT).hexdigest(), **extra,
    }


@pytest.fixture
def transport(tmp_path_factory, monkeypatch):
    # Keep the nested tenant/project/principal/transfer key under Windows'
    # legacy path limit; this suite exercises authorization, not long paths.
    tmp_path = tmp_path_factory.mktemp("ia")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "identity.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "strict")
    monkeypatch.setenv("REALYTICS_TENANT_ISOLATION_MODE", "strict")
    identities = {}
    for name, role, tenant in (
        ("owner", "analyst", TENANT_ID),
        ("colleague", "analyst", TENANT_ID),
        ("viewer", "viewer", TENANT_ID),
        ("reviewer", "reviewer", TENANT_ID),
        ("foreign", "admin", "foreign-tenant"),
    ):
        user_id = f"transfer-{name}"
        upsert_user({"userId": user_id, "tenantId": tenant, "role": role})
        session = create_user_session({"userId": user_id, "provider": "local-test"})
        identities[name] = {"x-realytics-session-id": session["sessionId"]}
    identities["guest"] = {}
    records = {
        project_id: {"project": {"id": project_id}, "_meta": {"tenantId": TENANT_ID}}
        for project_id in (PROJECT_ID, "second-project")
    }
    monkeypatch.setattr(projects, "project_repository", lambda request: SimpleNamespace(get=records.get))
    store = IntakeTransferStore(LocalObjectStorage(tmp_path / "o"))
    monkeypatch.setattr(projects, "intake_transfer_store", lambda: store)
    app = FastAPI()
    app.include_router(projects.router)
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client, identities=identities, store=store, records=records,
            objects=tmp_path / "o",
        )


def _request(transport, operation, headers, *, transfer_id="a" * 32, project_id=PROJECT_ID):
    base = f"/projects/{project_id}/intake/transfers"
    if operation == "create":
        return transport.client.post(base, headers=headers, json=_metadata())
    if operation == "part":
        return transport.client.put(
            f"{base}/{transfer_id}/parts/0",
            headers={**headers, "content-type": "application/octet-stream"}, content=CONTENT,
        )
    if operation == "cancel":
        return transport.client.delete(f"{base}/{transfer_id}", headers=headers)
    return transport.client.post(
        f"{base}/{transfer_id}/{operation}", headers=headers,
        json={"documentType": "business_plan"},
    )


def _stage(transport):
    owner = transport.identities["owner"]
    created = _request(transport, "create", owner)
    assert created.status_code == 200, created.text
    transfer_id = created.json()["transferId"]
    part = _request(transport, "part", owner, transfer_id=transfer_id)
    assert part.status_code == 200, part.text
    return transfer_id


def _assert_owner_bytes_intact(transport, transfer_id):
    scope = {"projectId": PROJECT_ID, "tenantId": TENANT_ID, "principalId": "transfer-owner"}
    with transport.store.assembled_file(scope, transfer_id) as (_, source):
        assert source.read() == CONTENT


@pytest.mark.parametrize("identity", ["viewer", "reviewer", "foreign", "guest"])
def test_every_transfer_endpoint_checks_permission_and_tenant_before_storage(transport, monkeypatch, identity):
    def forbidden_storage():
        pytest.fail("Unauthorized request reached the transfer storage factory")

    monkeypatch.setattr(projects, "intake_transfer_store", forbidden_storage)
    for operation in OPERATIONS:
        response = _request(transport, operation, transport.identities[identity])
        assert response.status_code == 403, (identity, operation, response.text)
        detail = response.json()["detail"]
        if identity == "foreign":
            assert detail["error"] == "tenant_scope_mismatch"
        else:
            assert detail["error"] == "forbidden"
            assert detail["requiredPermission"] == "project:write"


def test_other_user_cannot_reuse_owner_transfer_even_with_spoofed_identity(transport):
    transfer_id = _stage(transport)
    headers = {
        **transport.identities["colleague"],
        "x-realytics-user-id": "transfer-owner", "x-user-id": "transfer-owner",
        "x-realytics-tenant-id": TENANT_ID, "x-realytics-role": "admin",
    }
    for operation in ("part", "classify", "process"):
        response = _request(transport, operation, headers, transfer_id=transfer_id)
        assert response.status_code == 404, (operation, response.text)
        assert response.json()["detail"]["error"] == "intake_transfer_not_found"
    # Cancellation is deliberately idempotent in the caller's namespace. A
    # foreign id reveals nothing and cannot delete the real owner's staging.
    cancelled = _request(transport, "cancel", headers, transfer_id=transfer_id)
    assert cancelled.status_code == 200
    _assert_owner_bytes_intact(transport, transfer_id)


def test_same_user_cannot_move_transfer_between_projects(transport):
    transfer_id = _stage(transport)
    for operation in ("part", "classify", "process"):
        response = _request(
            transport, operation, transport.identities["owner"],
            transfer_id=transfer_id, project_id="second-project",
        )
        assert response.status_code == 404, (operation, response.text)
        assert response.json()["detail"]["error"] == "intake_transfer_not_found"
    cancelled = _request(
        transport, "cancel", transport.identities["owner"],
        transfer_id=transfer_id, project_id="second-project",
    )
    assert cancelled.status_code == 200
    _assert_owner_bytes_intact(transport, transfer_id)


def test_create_uses_verified_session_and_project_not_caller_scope(transport):
    headers = {
        **transport.identities["owner"], "x-realytics-user-id": "transfer-colleague",
        "x-realytics-tenant-id": "foreign-tenant", "x-realytics-role": "admin",
    }
    response = transport.client.post(
        f"/projects/{PROJECT_ID}/intake/transfers", headers=headers,
        json=_metadata(
            tenantId="foreign-tenant", principalId="transfer-colleague", projectId="second-project",
            scope={"tenantId": "foreign-tenant", "principalId": "transfer-colleague", "projectId": "second-project"},
        ),
    )
    assert response.status_code == 200, response.text
    manifests = list(transport.objects.rglob("manifest"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_bytes())
    assert manifest["scope"] == {
        "projectId": PROJECT_ID, "tenantId": TENANT_ID, "principalId": "transfer-owner",
    }
    transfer_id = response.json()["transferId"]
    part = _request(transport, "part", transport.identities["owner"], transfer_id=transfer_id)
    assert part.status_code == 200, part.text
    _assert_owner_bytes_intact(transport, transfer_id)


def test_revoked_session_cannot_continue_previously_created_transfer(transport, monkeypatch):
    transfer_id = _stage(transport)
    owner = transport.identities["owner"]
    assert revoke_user_session(owner["x-realytics-session-id"]) is True

    def forbidden_storage():
        pytest.fail("Revoked session reached staged source bytes")

    monkeypatch.setattr(projects, "intake_transfer_store", forbidden_storage)
    for operation in OPERATIONS:
        response = _request(transport, operation, owner, transfer_id=transfer_id)
        assert response.status_code == 403, (operation, response.text)
    _assert_owner_bytes_intact(transport, transfer_id)


def test_raw_identity_headers_cannot_authenticate_in_hardened_identity_lane(transport, monkeypatch):
    monkeypatch.setenv("REALYTICS_ENABLE_LOCAL_AUTH", "false")

    def forbidden_storage():
        pytest.fail("Unverified identity reached staged source bytes")

    monkeypatch.setattr(projects, "intake_transfer_store", forbidden_storage)
    headers = {
        "x-realytics-user-id": "transfer-owner", "x-realytics-tenant-id": TENANT_ID,
        "x-realytics-role": "admin",
    }
    for operation in OPERATIONS:
        response = _request(transport, operation, headers)
        assert response.status_code == 401, (operation, response.text)
        assert response.json()["detail"]["error"] == "raw_identity_header_disabled"
