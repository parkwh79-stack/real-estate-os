from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from agent_platform.contracts import (
    DataClassification,
    ToolCallStatus,
    ToolManifest,
    ToolRequest,
    ToolResult,
    ToolSideEffectLevel,
)
from agent_platform.registry import ToolRegistry


class _ProjectLedgerReadTool:
    def __init__(
        self,
        *,
        manifest: ToolManifest,
        reader: Callable[[str, str, dict[str, Any]], list[dict[str, Any]]],
    ) -> None:
        self.manifest = manifest
        self._reader = reader

    def execute(self, request: ToolRequest) -> ToolResult:
        started_at = datetime.now(timezone.utc)
        if not request.project_id:
            raise ValueError("TOOL_PROJECT_ID_REQUIRED")
        payload_project_id = request.payload.get("projectId")
        if payload_project_id and str(payload_project_id) != request.project_id:
            raise ValueError("TOOL_PROJECT_SCOPE_MISMATCH")
        records = self._reader(
            request.tenant_id,
            request.project_id,
            request.payload,
        )
        return ToolResult(
            tool_call_id=request.tool_call_id,
            status=ToolCallStatus.COMPLETED,
            output={"projectId": request.project_id, "records": records},
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            metadata={"recordCount": len(records), "source": "server_ledger"},
        )


def build_default_tool_registry() -> ToolRegistry:
    """Build the production registry from real, read-only server ledgers."""
    registry = ToolRegistry()
    registry.register(_ProjectLedgerReadTool(
        manifest=_read_manifest(
            "deal_state_records.read",
            "Deal State 원장 조회",
            "프로젝트의 Evidence, Assumption, Metric, RiskIssue 원장을 조회합니다.",
            "DealStateReadRequest",
            "DealStateReadResult",
        ),
        reader=_read_deal_state,
    ))
    registry.register(_ProjectLedgerReadTool(
        manifest=_read_manifest(
            "calculation_runs.read",
            "CalculationRun 원장 조회",
            "프로젝트의 결정론적 계산 실행 이력과 엔진 버전을 조회합니다.",
            "CalculationRunReadRequest",
            "CalculationRunReadResult",
        ),
        reader=_read_calculation_runs,
    ))
    return registry


def _read_manifest(
    tool_id: str,
    display_name: str,
    description: str,
    input_schema: str,
    output_schema: str,
) -> ToolManifest:
    return ToolManifest(
        tool_id=tool_id,
        version="1.0.0",
        display_name=display_name,
        description=description,
        input_schema_name=input_schema,
        output_schema_name=output_schema,
        side_effect_level=ToolSideEffectLevel.READ_ONLY,
        supported_data_classifications=list(DataClassification),
        required_roles=["admin", "reviewer", "analyst", "viewer"],
        timeout_seconds=30,
        idempotent=True,
        metadata={"sourceOfTruth": "server_ledger", "mock": False},
    )


def _read_deal_state(
    tenant_id: str,
    project_id: str,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    from storage import list_deal_state_records

    del tenant_id
    return list_deal_state_records(
        project_id,
        record_type=_optional(payload.get("recordType")),
        calculation_run_id=_optional(payload.get("calculationRunId")),
        review_status=_optional(payload.get("reviewStatus")),
        missing_locator=payload.get("missingLocator") if isinstance(payload.get("missingLocator"), bool) else None,
        latest_only=bool(payload.get("latestOnly")),
        limit=_limit(payload.get("limit"), 200),
    )


def _read_calculation_runs(
    tenant_id: str,
    project_id: str,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    from storage import list_calculation_run_records

    return list_calculation_run_records(
        project_id,
        tenant_id=tenant_id,
        engine_key=_optional(payload.get("engineKey")),
        limit=_limit(payload.get("limit"), 100),
    )


def _optional(value: Any) -> str | None:
    return str(value) if value not in {None, ""} else None


def _limit(value: Any, default: int) -> int:
    try:
        return max(1, min(int(value or default), 1000))
    except (TypeError, ValueError):
        return default
