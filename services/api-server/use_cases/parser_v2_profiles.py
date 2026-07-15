from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from parsing_platform.orchestrator import (
    ParsingOrchestrator,
    ParsingStageProfile,
    ProjectParsingProfile,
)
from storage import list_deal_state_records, upsert_deal_state_record


PROJECT_PARSING_PROFILE_RECORD_TYPE = "parser_v2_project_profile"


class ProjectParsingProfileConflict(ValueError):
    pass


def save_project_parsing_profile(
    project_id: str,
    tenant_id: str,
    actor_id: str,
    profile_value: Mapping[str, Any] | ProjectParsingProfile,
    orchestrator: ParsingOrchestrator,
    *,
    upsert_fn: Callable[[str, dict[str, Any]], dict[str, Any]] = upsert_deal_state_record,
    list_fn: Callable[..., list[dict[str, Any]]] = list_deal_state_records,
) -> dict[str, Any]:
    profile = ProjectParsingProfile.from_value(profile_value)
    if profile.project_id and profile.project_id != project_id:
        raise ValueError("project parsing profile belongs to another project")
    scoped_profile = ProjectParsingProfile(
        profile_id=profile.profile_id,
        version=profile.version,
        selected_modules=profile.selected_modules,
        excluded_modules=profile.excluded_modules,
        ordered_stages=profile.ordered_stages,
        failure_policy=profile.failure_policy,
        project_id=project_id,
        document_types=profile.document_types,
        asset_types=profile.asset_types,
        deal_types=profile.deal_types,
        capability_bindings=profile.capability_bindings,
        metadata=profile.metadata,
    )
    initial_plan = orchestrator.build_plan(scoped_profile)
    pinned_by_id = {
        entry.module_id: entry.manifest.ref for entry in initial_plan.modules
    }
    pinned_bindings = {
        capability: tuple(
            pinned_by_id.get(ref.partition("@")[0], ref)
            for ref in module_refs
        )
        for capability, module_refs in scoped_profile.capability_bindings.items()
    }
    canonical_profile = ProjectParsingProfile(
        profile_id=scoped_profile.profile_id,
        version=scoped_profile.version,
        selected_modules=tuple(entry.manifest.ref for entry in initial_plan.modules),
        excluded_modules=scoped_profile.excluded_modules,
        ordered_stages=tuple(
            ParsingStageProfile(
                stage_id=stage.stage_id,
                module_refs=tuple(entry.manifest.ref for entry in stage.modules),
                failure_policy=stage.failure_policy,
            )
            for stage in initial_plan.stages
        ),
        failure_policy=scoped_profile.failure_policy,
        project_id=project_id,
        document_types=scoped_profile.document_types,
        asset_types=scoped_profile.asset_types,
        deal_types=scoped_profile.deal_types,
        capability_bindings=pinned_bindings,
        metadata={
            **dict(scoped_profile.metadata),
            "moduleVersionsPinned": True,
        },
    )
    plan = orchestrator.build_plan(canonical_profile)
    profile_payload = canonical_profile.to_dict()
    profile_hash = _hash(profile_payload)
    execution_plan_hash = _hash(plan.to_dict())
    record_id = f"parser_v2_project_profile:{canonical_profile.profile_id}:{canonical_profile.version}"
    existing = next(
        (
            record
            for record in list_fn(project_id, record_type=PROJECT_PARSING_PROFILE_RECORD_TYPE, limit=1000)
            if record.get("recordId") == record_id
        ),
        None,
    )
    if existing:
        if existing.get("profileHash") != profile_hash or existing.get("tenantId") != tenant_id:
            raise ProjectParsingProfileConflict(
                "A project parsing profile version is immutable; create a new version for changes."
            )
        return existing

    now = datetime.now(timezone.utc).isoformat()
    record = {
        "schemaVersion": "realytics-project-parsing-profile-v1",
        "recordType": PROJECT_PARSING_PROFILE_RECORD_TYPE,
        "recordId": record_id,
        "key": f"{canonical_profile.profile_id}@{canonical_profile.version}",
        "projectId": project_id,
        "tenantId": tenant_id,
        "actorId": actor_id,
        "configurationStatus": "active",
        "profileHash": profile_hash,
        "executionPlanHash": execution_plan_hash,
        "profile": profile_payload,
        "executionPlan": plan.to_dict(),
        "sourceType": "human_configured_parser_profile",
        "createdAt": now,
        "updatedAt": now,
    }
    return upsert_fn(project_id, record)


def list_project_parsing_profiles(
    project_id: str,
    tenant_id: str,
    *,
    list_fn: Callable[..., list[dict[str, Any]]] = list_deal_state_records,
) -> list[dict[str, Any]]:
    records = list_fn(project_id, record_type=PROJECT_PARSING_PROFILE_RECORD_TYPE, limit=1000)
    return [record for record in records if record.get("tenantId") == tenant_id]


def get_project_parsing_profile(
    project_id: str,
    tenant_id: str,
    profile_id: str,
    version: str,
    *,
    list_fn: Callable[..., list[dict[str, Any]]] = list_deal_state_records,
) -> ProjectParsingProfile:
    expected = f"parser_v2_project_profile:{profile_id}:{version}"
    record = next(
        (
            item
            for item in list_project_parsing_profiles(project_id, tenant_id, list_fn=list_fn)
            if item.get("recordId") == expected
        ),
        None,
    )
    if not record:
        raise LookupError("Project parsing profile not found")
    return ProjectParsingProfile.from_value(record["profile"])


def _hash(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
