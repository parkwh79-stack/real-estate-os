"""Project-authorized saved selections of immutable CalculationRun references."""
from datetime import datetime, timezone
import hmac
from threading import RLock

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from core.authz import authorize_request, enforce_project_tenant_scope
from db.repositories.calculation_run_repository import CalculationRunRepositoryError
from db.repositories.postgres_project_repository import ProjectVersionConflict
from routers import projects
from storage import list_calculation_run_records, storage_backend
from use_cases.cash_flow_comparison import (
    SELECTION_KEY, compare_cash_flow_runs, comparison_state_hash, run_issues, run_summary,
)

router = APIRouter(prefix="/projects", tags=["cash flow comparison"])
_selection_lock = RLock()  # Local compatibility writes; PostgreSQL retains state-version CAS.


class ComparisonSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    baselineRunId: str = Field(min_length=1, max_length=256)
    comparisonRunId: str = Field(min_length=1, max_length=256)
    expectedComparisonHash: str = Field(pattern=r"^[a-f0-9]{64}$")


def _context(project_id: str, request: Request, permission: str):
    auth = authorize_request(request, permission)
    repository = projects.project_repository()
    project = repository.get(project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    context = enforce_project_tenant_scope(request, project)
    tenant_id = (project.get("_meta") or {}).get("tenantId") or (project.get("_meta") or {}).get("tenant_id")
    actor_tenant = (context.get("user") or {}).get("tenantId")
    if tenant_id and actor_tenant and str(tenant_id) != str(actor_tenant):
        raise HTTPException(403, {"error": "tenant_scope_mismatch"})
    if storage_backend() in {"postgres", "postgresql"} and not tenant_id:
        raise HTTPException(401, {"error": "tenant_required"})
    return repository, project, str(tenant_id or "legacy"), (auth.get("user") or {}).get("userId")


def _records(project_id: str, tenant_id: str, run_id: str | None = None):
    try:
        records = list_calculation_run_records(project_id, tenant_id=tenant_id,
            calculation_run_id=run_id, engine_key=None if run_id else "cash_flow_engine", limit=500)
    except CalculationRunRepositoryError as exc:
        raise HTTPException(503, {"error": "calculation_run_authority_unavailable"}) from exc
    scoped = [row for row in records if str(row.get("projectId")) == project_id
              and str(row.get("tenantId") or "legacy") == tenant_id]
    try:
        comparison_state_hash({}, scoped)
    except (TypeError, ValueError) as exc:
        raise HTTPException(503, {"error": "invalid_calculation_run_authority"}) from exc
    return scoped


def _exact_run(project_id: str, tenant_id: str, run_id: str) -> dict:
    records = _records(project_id, tenant_id, run_id)
    if len(records) != 1 or records[0].get("calculationRunId") != run_id:
        raise HTTPException(404, {"error": "calculation_run_not_found"})
    return records[0]


def _state(project_id: str, project: dict, tenant_id: str, *, baseline_id=None, comparison_id=None):
    records = _records(project_id, tenant_id)
    authority_records = {row["calculationRunId"]: row for row in records}
    saved = (project.get("_meta") or {}).get(SELECTION_KEY) or None
    # Saved or explicitly selected runs may predate the latest 500-row selector.
    # Bind their exact current snapshots into the same optimistic state token.
    if saved:
        for key in ("baselineRunId", "comparisonRunId"):
            try:
                row = _exact_run(project_id, tenant_id, saved[key])
                authority_records[row["calculationRunId"]] = row
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
    preview = baseline_id is not None or comparison_id is not None
    if preview and not (baseline_id and comparison_id):
        raise HTTPException(422, {"error": "both_calculation_run_ids_required"})
    selected = {"baselineRunId": baseline_id, "comparisonRunId": comparison_id} if preview else saved
    comparison = None
    if selected:
        try:
            baseline = _exact_run(project_id, tenant_id, selected["baselineRunId"])
            changed = _exact_run(project_id, tenant_id, selected["comparisonRunId"])
            authority_records[baseline["calculationRunId"]] = baseline
            authority_records[changed["calculationRunId"]] = changed
            comparison = compare_cash_flow_runs(baseline, changed, saved_selection=None if preview else saved)
        except HTTPException as exc:
            if preview or exc.status_code != 404:
                raise
            comparison = {"status": "incompatible", "reasons": ["saved_calculation_missing"], "metrics": [], "inputChanges": []}
    return {"schemaVersion": "realytics-cash-flow-comparison-v1", "projectId": project_id,
        "comparisonHash": comparison_state_hash(project, list(authority_records.values())),
        "selection": {**selected, "saved": not preview} if selected else None,
        "availableRuns": [run_summary(row) for row in authority_records.values() if not run_issues(row)],
        "comparison": comparison}


@router.get("/{project_id}/cash-flow/comparison")
def get_cash_flow_comparison(project_id: str, request: Request, response: Response,
    baselineRunId: str | None = Query(default=None, max_length=256),
    comparisonRunId: str | None = Query(default=None, max_length=256)):
    response.headers["Cache-Control"] = "no-store"
    _, project, tenant, _ = _context(project_id, request, "project:read")
    return _state(project_id, project, tenant, baseline_id=baselineRunId, comparison_id=comparisonRunId)


@router.post("/{project_id}/cash-flow/comparison")
def save_cash_flow_comparison(project_id: str, request: Request, response: Response, input_data: ComparisonSelection):
    response.headers["Cache-Control"] = "no-store"
    with _selection_lock:
        repository, project, tenant, actor = _context(project_id, request, "project:write")
        preview = _state(project_id, project, tenant,
            baseline_id=input_data.baselineRunId, comparison_id=input_data.comparisonRunId)
        if not hmac.compare_digest(input_data.expectedComparisonHash, preview["comparisonHash"]):
            raise HTTPException(409, {"error": "comparison_state_conflict", "message": "프로젝트 또는 계산 기록이 변경되었습니다. 다시 불러온 뒤 저장해 주세요."})
        comparison = preview["comparison"]
        if comparison["status"] == "incompatible":
            raise HTTPException(422, {"error": "incompatible_calculation_runs", "reasons": comparison["reasons"]})
        selection = {"baselineRunId": input_data.baselineRunId, "comparisonRunId": input_data.comparisonRunId,
            "savedAt": datetime.now(timezone.utc).isoformat(), "savedBy": actor}
        for side in ("baseline", "comparison"):
            selection[side + "InputHash"] = comparison[side]["inputHash"]
            selection[side + "ResultHash"] = comparison[side]["resultHash"]
        # Store references only. The existing repository keeps tenant/version authority.
        try:
            updated = repository.update(project_id, {"_meta": {SELECTION_KEY: selection}})
        except ProjectVersionConflict as exc:
            raise HTTPException(409, {"error": "project_version_conflict"}) from exc
        if updated is None:
            raise HTTPException(404, "Project not found")
        projects.record_sensitive_project_event(request, event_type="cash_flow_comparison_saved",
            project_id=project_id, metadata={**selection, "comparisonStatus": comparison["status"],
                "reasons": comparison["reasons"]})
        return _state(project_id, updated, tenant)
