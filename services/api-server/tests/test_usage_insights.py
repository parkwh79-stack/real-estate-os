from use_cases.usage_insights import (
    build_usage_insights,
    normalize_path,
    render_optimization_log_markdown,
    KNOWN_WORKFLOWS,
)


def test_normalize_path_strips_ids_and_uuids():
    assert normalize_path("/projects/123/orchestration/run-workflow") == "/projects/{id}/orchestration/run-workflow"
    assert normalize_path("/projects/9f1c2e7a-1111-2222-3333-444455556666/report") == "/projects/{id}/report"
    assert normalize_path(None) == "(unknown)"


def _logs():
    return [
        {"eventType": "api_request", "userId": "u1", "projectId": "p1", "path": "/projects/1/returns/calculate",
         "statusCode": 200, "durationMs": 120, "createdAt": "2026-06-20T01:00:00Z"},
        {"eventType": "api_request", "userId": "u1", "projectId": "p1", "path": "/projects/1/orchestrations/pf-review",
         "statusCode": 200, "durationMs": 4200, "createdAt": "2026-06-20T01:01:00Z"},
        {"eventType": "api_request", "userId": "u2", "projectId": "p2", "path": "/projects/2/orchestrations/pf-review",
         "statusCode": 500, "durationMs": 3800, "createdAt": "2026-06-20T02:00:00Z"},
        {"eventType": "kakao_message_preview", "userId": "u2", "projectId": "p2", "path": "/projects/2/kakao/intake-plan",
         "statusCode": 200, "durationMs": 50, "metadata": {"recommendedWorkflow": "feasibility"},
         "createdAt": "2026-06-20T02:01:00Z"},
        {"eventType": "api_request", "userId": "u2", "projectId": "p2", "path": "/projects/2/orchestration/run-workflow",
         "statusCode": 200, "durationMs": 90, "metadata": {"workflow": "feasibility", "blockerCodes": ["LAND_EVIDENCE_READY", "UNSOLD_LTV"]},
         "createdAt": "2026-06-20T02:02:00Z"},
    ]


def test_build_usage_insights_aggregates_core_signals():
    insights = build_usage_insights(_logs())
    assert insights["totals"]["events"] == 5
    assert insights["totals"]["distinctUsers"] == 2
    assert insights["totals"]["distinctProjects"] == 2
    # pf-review endpoint aggregates both projects under one normalized path.
    pf = next(r for r in insights["endpointUsage"] if r["endpoint"] == "/projects/{id}/orchestrations/pf-review")
    assert pf["count"] == 2
    # workflow selection captured from metadata.
    assert any(r["workflow"] == "feasibility" and r["count"] == 2 for r in insights["workflowSelections"])


def test_slow_endpoint_and_error_hotspot_detected():
    insights = build_usage_insights(_logs())
    slow = next(r for r in insights["slowEndpoints"] if r["endpoint"] == "/projects/{id}/orchestrations/pf-review")
    assert slow["verySlowCount"] >= 1  # 4200ms and 3800ms both > 3000
    assert slow["maxMs"] == 4200.0
    errors = next(r for r in insights["errorHotspots"] if r["endpoint"] == "/projects/{id}/orchestrations/pf-review")
    assert errors["count"] == 1  # the 500


def test_recurring_blockers_and_unused_workflows():
    insights = build_usage_insights(_logs())
    blockers = {r["blocker"] for r in insights["recurringBlockers"]}
    assert "UNSOLD_LTV" in blockers and "LAND_EVIDENCE_READY" in blockers
    # only "feasibility" was used; the rest are flagged unused.
    assert set(insights["unusedWorkflows"]) == set(KNOWN_WORKFLOWS) - {"feasibility"}


def test_recommendations_present_and_render_is_safe():
    insights = build_usage_insights(_logs())
    assert insights["recommendations"]
    md = render_optimization_log_markdown(insights, "2026-06-20 12:00")
    assert "사용 기반 최적화 리포트" in md
    assert "민감정보는 포함되지 않습니다" in md


def test_empty_logs_safe():
    insights = build_usage_insights([])
    assert insights["totals"]["events"] == 0
    assert insights["recommendations"][0]["severity"] == "info"
