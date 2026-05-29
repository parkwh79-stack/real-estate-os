from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict

from fastapi import APIRouter, Header, Query, Request, Response

from core.authz import authorize_request
from storage import list_user_activity_logs, record_user_activity_log


router = APIRouter(prefix="/audit", tags=["audit"])

SENSITIVE_EVENT_TYPES = {
    "source_document_download",
    "report_export_created",
    "report_export_download",
    "raw_evidence_access",
}


@router.get("/user-activity")
def get_user_activity_logs(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    user_id: str | None = Query(None),
    project_id: str | None = Query(None),
    event_type: str | None = Query(None),
):
    authorize_request(request, "audit:read")
    return {
        "schemaVersion": "realytics-user-activity-log-list-v1",
        "logs": list_user_activity_logs(
            limit=limit,
            user_id=user_id,
            project_id=project_id,
            event_type=event_type,
        ),
    }


@router.get("/user-activity/summary")
def get_user_activity_summary(
    request: Request,
    limit: int = Query(1000, ge=1, le=5000),
    user_id: str | None = Query(None),
    project_id: str | None = Query(None),
    event_type: str | None = Query(None),
):
    authorize_request(request, "audit:read")
    logs = list_user_activity_logs(
        limit=limit,
        user_id=user_id,
        project_id=project_id,
        event_type=event_type,
    )
    return {
        "schemaVersion": "realytics-user-activity-summary-v1",
        "filters": {
            "limit": limit,
            "userId": user_id,
            "projectId": project_id,
            "eventType": event_type,
        },
        "summary": summarize_activity_logs(logs),
    }


@router.get("/user-activity/export")
def export_user_activity_logs(
    request: Request,
    limit: int = Query(1000, ge=1, le=5000),
    user_id: str | None = Query(None),
    project_id: str | None = Query(None),
    event_type: str | None = Query(None),
    format: str = Query("json", pattern="^(json|csv)$"),
):
    authorize_request(request, "audit:read")
    logs = list_user_activity_logs(
        limit=limit,
        user_id=user_id,
        project_id=project_id,
        event_type=event_type,
    )
    if format == "csv":
        return Response(
            content=activity_logs_to_csv(logs),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="realytics-user-activity.csv"'},
        )

    return {
        "schemaVersion": "realytics-user-activity-export-v1",
        "logs": logs,
    }


@router.post("/login-event")
def record_login_event(
    payload: Dict[str, Any],
    x_user_id: str | None = Header(None),
    x_realytics_user_id: str | None = Header(None),
):
    user_id = payload.get("userId") or payload.get("user_id") or x_realytics_user_id or x_user_id or "anonymous"
    record = record_user_activity_log(
        {
            "eventType": "login",
            "userId": user_id,
            "metadata": {
                "loginStatus": payload.get("status") or "observed",
                "provider": payload.get("provider"),
                "tenantId": payload.get("tenantId") or payload.get("tenant_id"),
                "host": payload.get("host") or "api",
                "note": payload.get("note"),
            },
        }
    )
    return {
        "schemaVersion": "realytics-login-event-v1",
        "record": record,
    }


def summarize_activity_logs(logs: list[Dict[str, Any]]) -> Dict[str, Any]:
    event_type_counts: Dict[str, int] = {}
    user_counts: Dict[str, int] = {}
    project_counts: Dict[str, int] = {}
    status_buckets = {"success": 0, "client_error": 0, "server_error": 0, "unknown": 0}
    sensitive_events = 0
    slow_requests = 0

    for log in logs:
        event_type = str(log.get("eventType") or "unknown")
        event_type_counts[event_type] = event_type_counts.get(event_type, 0) + 1

        user_id = str(log.get("userId") or "unknown")
        user_counts[user_id] = user_counts.get(user_id, 0) + 1

        project_id = log.get("projectId")
        if project_id:
            key = str(project_id)
            project_counts[key] = project_counts.get(key, 0) + 1

        status_code = log.get("statusCode")
        if isinstance(status_code, int):
            if status_code >= 500:
                status_buckets["server_error"] += 1
            elif status_code >= 400:
                status_buckets["client_error"] += 1
            else:
                status_buckets["success"] += 1
        else:
            status_buckets["unknown"] += 1

        if event_type in SENSITIVE_EVENT_TYPES:
            sensitive_events += 1
        if (log.get("durationMs") or 0) >= 1000:
            slow_requests += 1

    return {
        "totalEvents": len(logs),
        "distinctUsers": len(user_counts),
        "distinctProjects": len(project_counts),
        "sensitiveEvents": sensitive_events,
        "slowRequests": slow_requests,
        "eventTypeCounts": sorted_count_rows(event_type_counts, "eventType"),
        "userCounts": sorted_count_rows(user_counts, "userId"),
        "projectCounts": sorted_count_rows(project_counts, "projectId"),
        "statusBuckets": status_buckets,
    }


def sorted_count_rows(counts: Dict[str, int], key_name: str) -> list[Dict[str, Any]]:
    return [
        {key_name: key, "count": value}
        for key, value in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def activity_logs_to_csv(logs: list[Dict[str, Any]]) -> str:
    output = io.StringIO()
    fieldnames = [
        "eventId",
        "eventType",
        "createdAt",
        "userId",
        "projectId",
        "runId",
        "method",
        "path",
        "statusCode",
        "durationMs",
        "clientHost",
        "requestId",
        "metadata",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for log in logs:
        writer.writerow({
            field: json.dumps(log.get(field), ensure_ascii=False) if field == "metadata" else log.get(field)
            for field in fieldnames
        })
    return output.getvalue()
