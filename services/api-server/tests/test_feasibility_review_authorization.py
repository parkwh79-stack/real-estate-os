"""Ordinary analysis edits cannot grant approval or cross tenant boundaries."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

import main
from core import authz
from routers import projects
from test_normalized_project_events import normalized_events  # noqa: F401


@pytest.fixture
def analysis_authority(normalized_events, monkeypatch):
    authority, events, client = normalized_events

    def identity(request):
        role = request.headers.get("x-test-role", "analyst")
        return {
            "authenticated": True,
            "role": role,
            "permissions": authz.permissions_for_role(role),
            "user": {"userId": "verified-analysis-user", "role": role,
                     "tenantId": request.headers.get("x-test-tenant", "tenant-a")},
        }

    monkeypatch.setenv("REALYTICS_RBAC_MODE", "strict")
    monkeypatch.setenv("REALYTICS_TENANT_ISOLATION_MODE", "strict")
    for module in (main, projects, authz):
        monkeypatch.setattr(module, "identity_context_from_request", identity)
    for module in (main, projects):
        monkeypatch.setattr(module, "enforce_project_tenant_scope", authz.enforce_project_tenant_scope)
    monkeypatch.setattr(projects, "authorize_request", authz.authorize_request)
    document = {"archiveId": "a" * 64, "originalFileName": "analysis-review.xlsx"}
    monkeypatch.setattr(projects, "source_document_repository", lambda: SimpleNamespace(
        list=lambda _project: [deepcopy(document)],
        find_by_archive_id=lambda project, archive: deepcopy(document)
        if project == "p" and archive == document["archiveId"] else None,
    ))
    return authority, events, client


def review_payload(client):
    response = client.get("/projects/p/feasibility-workbook/review")
    assert response.status_code == 200, response.text
    return {
        "expectedReviewHash": response.json()["reviewHash"],
        "feasibility_input": {"total_revenue": 2_000, "finance_cost": 100},
        "fieldReviews": {
            "total_revenue": {"origin": "document_correction", "reason": "원본 셀 확인",
                              "sourceCell": "손익!B1", "approvalStatus": "approved",
                              "needsReview": False, "reviewedBy": "forged-user"},
            "finance_cost": {"origin": "user_assumption", "reason": "분석용 비용 가정"},
        },
        "fieldSourceDocuments": {"total_revenue": {"archiveId": "a" * 64,
                                                        "originalFileName": "forged.xlsx"}},
        "canonicalFeasibilityStudyV2": {"forged": True},
        "approvedInputSnapshot": {"forged": True},
        "creditDecision": {"status": "approved"},
    }


@pytest.mark.parametrize("role", ["analyst", "reviewer"])
def test_analysis_role_saves_reopens_without_changing_approved_authority(analysis_authority, role):
    authority, events, client = analysis_authority
    protected = {
        "canonicalFeasibilityStudyV2": {"project_id": "p", "raw_import": {"canonicalStatus": "approved"}},
        "approvedInputSnapshot": {"snapshotId": "approved-before-edit", "inputHash": "b" * 64},
        "creditDecision": {"status": "hold", "reason": "existing-review"},
    }
    authority.row.update(deepcopy(protected))
    authority.row["_meta"]["feasibilityWorkbookApprovals"] = [{"importId": "approved-original"}]
    payload = review_payload(client)
    saved = client.post("/projects/p/feasibility-workbook/apply-input", json=payload,
                        headers={"x-test-role": role})
    assert saved.status_code == 200, saved.text
    assert saved.json()["analysisOnly"] is True and saved.json()["reportEligible"] is False
    assert saved.json()["requiresRecalculation"] is True
    reopened = client.get("/projects/p/feasibility-workbook/review").json()
    assert reopened["values"]["total_revenue"] == 2_000
    assert reopened["values"]["finance_cost"] == 100
    assert reopened["analysisOnly"] is True and reopened["reportEligible"] is False
    for field in ("total_revenue", "finance_cost"):
        review = reopened["fieldReviews"][field]
        assert review["needsReview"] is True
        assert review["approvalStatus"] == "not_requested"
        assert review["reviewedBy"] == "verified-analysis-user"
    assert reopened["fieldReviews"]["total_revenue"]["sourceDocument"]["originalFileName"] == "analysis-review.xlsx"
    assert {key: authority.row[key] for key in protected} == protected
    assert authority.row["_meta"]["feasibilityWorkbookApprovals"] == [{"importId": "approved-original"}]
    assert len(events) == 1
    assert events[0][1][1] == "feasibility_input_applied"
    assert events[0][1][2:4] == ("verified-analysis-user", "p")
    assert client.post("/projects/p/feasibility-workbook/apply-input", json=payload).status_code == 409
    assert len(events) == 1


def test_analyst_cannot_promote_formal_workbook_approval(analysis_authority):
    authority, events, client = analysis_authority
    before = deepcopy(authority.row)
    response = client.post("/projects/p/feasibility-workbook/approve", json={"approvedBy": "verified-analysis-user"})
    assert response.status_code == 403
    assert response.json()["detail"]["requiredPermission"] == "assumption:override"
    assert authority.row == before and events == []


@pytest.mark.parametrize("headers, expected_status", [
    ({"x-test-role": "viewer"}, 403),
    ({"x-test-tenant": "tenant-b"}, 404),
])
def test_analysis_edit_keeps_role_and_tenant_boundaries(analysis_authority, headers, expected_status):
    authority, events, client = analysis_authority
    payload = review_payload(client)
    before = deepcopy(authority.row)
    response = client.post("/projects/p/feasibility-workbook/apply-input", json=payload, headers=headers)
    assert response.status_code == expected_status, response.text
    assert authority.row == before and events == []


def test_analysis_edit_cannot_use_another_projects_archive(analysis_authority):
    authority, events, client = analysis_authority
    payload = review_payload(client)
    payload["fieldSourceDocuments"]["total_revenue"]["archiveId"] = "c" * 64
    before = deepcopy(authority.row)
    response = client.post("/projects/p/feasibility-workbook/apply-input", json=payload)
    assert response.status_code == 400
    assert authority.row == before and events == []
