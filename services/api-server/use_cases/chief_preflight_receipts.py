"""Hash-only receipts for browser-held Chief preflight progress."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
import binascii
import zlib
import json
import re
from secrets import token_hex
from typing import Any

from db import postgres
from storage import list_agent_handoff_records, storage_backend, upsert_agent_handoff_record
from use_cases.chief_pod_records import stable_digest


RECEIPT_TYPE = "chief_preflight_receipt"
RECEIPT_SCHEMA = "realytics-chief-preflight-receipt-v1"
MAX_STATE_BYTES = 512 * 1024
COMPRESS_STATE_BYTES = 384 * 1024
MAX_UNPACKED_BYTES = 4 * 1024 * 1024
RECEIPT_LIFETIME = timedelta(minutes=30)


class PreflightReceiptError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _state_hash(state: Any) -> str:
    if not isinstance(state, dict):
        raise PreflightReceiptError("이전 검토 상태가 올바르지 않습니다. 새 검토를 시작하세요.")
    try:
        encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise PreflightReceiptError("이전 검토 상태를 확인할 수 없습니다. 새 검토를 시작하세요.") from exc
    if len(encoded) > MAX_STATE_BYTES:
        raise PreflightReceiptError("이전 검토 상태가 너무 큽니다. 새 검토를 시작하세요.")
    return stable_digest(state)



def pack_review_state(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise PreflightReceiptError("검토 결과를 확인할 수 없습니다.", status_code=503)
    encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_UNPACKED_BYTES:
        raise PreflightReceiptError("검토 결과가 너무 큽니다. 검토 범위를 줄여 다시 실행하세요.", status_code=413)
    if len(encoded) <= COMPRESS_STATE_BYTES:
        return state
    packed = {
        "runnerVersion": state.get("runnerVersion"), "sourceRevision": state.get("sourceRevision"),
        "workflowTrack": state.get("workflowTrack"), "encoding": "zlib-base64-v1",
        "data": base64.b64encode(zlib.compress(encoded)).decode("ascii"),
    }
    _state_hash(packed)
    return packed


def _unpack_review_state(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("encoding") != "zlib-base64-v1":
        return state
    try:
        content = base64.b64decode(state["data"], validate=True)
        decompressor = zlib.decompressobj()
        decoded = decompressor.decompress(content, MAX_UNPACKED_BYTES + 1)
        if len(decoded) > MAX_UNPACKED_BYTES or decompressor.unconsumed_tail or not decompressor.eof:
            raise ValueError("Invalid review size")
        value = json.loads(decoded)
        if not isinstance(value, dict) or any(value.get(key) != state.get(key) for key in ("runnerVersion", "sourceRevision", "workflowTrack")):
            raise ValueError("Invalid review lineage")
    except (ValueError, TypeError, KeyError, zlib.error, binascii.Error) as exc:
        raise PreflightReceiptError("이전 검토 내용을 확인하지 못했습니다. 새 검토를 시작하세요.", status_code=409) from exc
    return value


def _read_handoff(project_id: str, receipt_id: str) -> dict[str, Any] | None:
    if storage_backend() in {"postgres", "postgresql"}:
        return postgres.get_typed_jsonb_record("agent_handoffs", {
            "project_id": project_id, "handoff_id": receipt_id,
        })
    return next((
        record for record in list_agent_handoff_records(project_id, from_agent="ChiefPreflightReviewer", limit=1000)
        if record.get("handoffId") == receipt_id
    ), None)


def _insert_handoff(project_id: str, record: dict[str, Any]) -> None:
    if storage_backend() in {"postgres", "postgresql"}:
        inserted = postgres.insert_typed_jsonb_record("agent_handoffs", {
            "tenant_id": record["tenantId"],
            "project_id": project_id,
            "handoff_id": record["handoffId"],
            "from_agent": record["fromAgent"],
            "to_agent": record["toAgent"],
            "handoff_type": RECEIPT_TYPE,
            "status": "created",
            "created_at": record["createdAt"],
            "updated_at": record["updatedAt"],
        }, record)
        if not inserted:
            raise PreflightReceiptError("검토 확인 기록이 충돌했습니다. 검토를 다시 시도하세요.", status_code=409)
        return
    if _read_handoff(project_id, record["handoffId"]) is not None:
        raise PreflightReceiptError("검토 확인 기록이 충돌했습니다. 검토를 다시 시도하세요.", status_code=409)
    upsert_agent_handoff_record(project_id, record)


def verify_previous_review(
    previous: Any, *, project_id: str, tenant_id: str, actor_id: str,
    workflow_track: str, engine_version: str, input_revision: str,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    if previous is None:
        return None
    if (
        not isinstance(previous, dict) or set(previous) != {"receiptId", "resumeState"}
        or not isinstance(previous["receiptId"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", previous["receiptId"])
    ):
        raise PreflightReceiptError("이전 검토 확인 정보가 올바르지 않습니다. 새 검토를 시작하세요.")
    state_hash = _state_hash(previous["resumeState"])
    record = _read_handoff(project_id, previous["receiptId"])
    if (
        not isinstance(record, dict) or record.get("handoffType") != RECEIPT_TYPE
        or record.get("handoffId") != previous["receiptId"]
        or record.get("projectId") != project_id or record.get("tenantId") != tenant_id
    ):
        raise PreflightReceiptError("이전 검토를 확인할 수 없습니다. 새 검토를 시작하세요.", status_code=409)
    payload = record.get("payload")
    # The signed state belongs to its original source; the runner invalidates changed Pods.
    expected = {
        "projectId": project_id, "tenantId": tenant_id, "actorId": actor_id,
        "workflowTrack": workflow_track, "engineVersion": engine_version,
        "stateHash": state_hash, "inputRevision": previous["resumeState"].get("sourceRevision"),
    }
    if not isinstance(payload, dict) or any(payload.get(key) != value for key, value in expected.items()):
        raise PreflightReceiptError("검토 상태나 입력 기준이 달라졌습니다. 새 검토를 시작하세요.", status_code=409)
    current = now or datetime.now(timezone.utc)
    try:
        created = datetime.fromisoformat(payload["createdAt"])
        expires = datetime.fromisoformat(payload["expiresAt"])
        valid = created <= current < expires and expires - created == RECEIPT_LIFETIME
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise PreflightReceiptError("이전 검토의 유효 시간이 지났습니다. 새 검토를 시작하세요.", status_code=409)
    return _unpack_review_state(previous["resumeState"])


def issue_review_receipt(
    resume_state: Any, *, project_id: str, tenant_id: str, actor_id: str,
    workflow_track: str, engine_version: str, input_revision: str,
    now: datetime | None = None,
) -> str:
    state_hash = _state_hash(resume_state)
    if resume_state.get("sourceRevision") != input_revision or resume_state.get("runnerVersion") != engine_version:
        raise PreflightReceiptError("검토 상태의 실행 기준을 확인하지 못했습니다. 다시 시도하세요.", status_code=503)
    created = now or datetime.now(timezone.utc)
    receipt_id = token_hex(32)
    payload = {
        "projectId": project_id, "tenantId": tenant_id, "actorId": actor_id,
        "workflowTrack": workflow_track, "engineVersion": engine_version,
        "stateHash": state_hash, "inputRevision": input_revision,
        "createdAt": created.isoformat(), "expiresAt": (created + RECEIPT_LIFETIME).isoformat(),
    }
    record = {
        "schemaVersion": "realytics-agent-handoff-v1",
        "handoffId": receipt_id, "projectId": project_id, "tenantId": tenant_id,
        "workflow": "chief-deal-orchestration", "handoffType": RECEIPT_TYPE,
        "fromAgent": "ChiefPreflightReviewer", "toAgent": "ChiefPreflightReviewer",
        "status": "created", "payloadSchema": RECEIPT_SCHEMA, "payload": payload,
        "createdAt": created.isoformat(), "updatedAt": created.isoformat(),
    }
    _insert_handoff(project_id, record)
    return receipt_id
