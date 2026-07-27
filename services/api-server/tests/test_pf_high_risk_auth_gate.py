from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from routers import projects


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


def _aal1_context() -> dict:
    return {
        "authenticated": True,
        "role": "reviewer",
        "permissions": ["project:review"],
        "user": {
            "userId": "checker-aal1",
            "tenantId": "tenant-1",
            "metadata": {"principalType": "human"},
        },
        "identityAssurance": {
            "aal": "aal1",
            "mfaVerified": False,
            "authenticatedAt": None,
            "source": "verified_bearer_claims",
        },
    }


@pytest.mark.parametrize(
    ("route", "route_args", "expected_action"),
    [
        (
            projects.approve_project_orchestration,
            ("project-aal-gate", "run-1"),
            "pf_orchestration_final_approve",
        ),
        (
            projects.publish_report,
            ("project-aal-gate", "report-1"),
            "pf_report_final_publish",
        ),
    ],
)
def test_final_pf_actions_reject_aal1_before_loading_authoritative_record(
    monkeypatch,
    route,
    route_args,
    expected_action,
):
    monkeypatch.setenv("REALYTICS_REQUIRE_HIGH_RISK_STEP_UP", "true")
    monkeypatch.setenv("REALYTICS_STEP_UP_MAX_AGE_SECONDS", "900")
    monkeypatch.setitem(
        projects.PROJECTS_DB,
        "project-aal-gate",
        {"id": "project-aal-gate", "_meta": {"tenantId": "tenant-1"}},
    )
    monkeypatch.setattr(
        projects,
        "authorize_request",
        lambda _request, _permission: _aal1_context(),
    )
    monkeypatch.setattr(
        projects,
        "enforce_project_tenant_scope",
        lambda _request, _project: _aal1_context(),
    )

    with pytest.raises(HTTPException) as error:
        route(*route_args, _request())

    assert error.value.status_code == 403
    assert error.value.detail == {
        "error": "step_up_required",
        "action": expected_action,
        "reason": "aal2_required",
    }
