from __future__ import annotations

"""Dependency-aware orchestration for modular real-estate document parsing.

The orchestrator is intentionally infrastructure-neutral.  It builds a
deterministic plan from a project profile, executes sync or async modules one
stage at a time, and returns auditable per-module results and traces.
"""

import asyncio
import hashlib
import inspect
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from parsing_platform.document_taxonomy import (
    DOCUMENT_TAXONOMY_VERSION,
    DOCUMENT_TYPE_SCOPE_ALL,
    DOCUMENT_TYPE_SCOPE_LISTED,
    canonical_document_type,
    canonical_document_types,
)
from parsing_platform.registry import (
    FAILURE_POLICIES,
    ParserModuleManifest,
    ParserModuleRegistry,
    ParsingModule,
    UnknownModuleError,
    split_module_ref,
)
from parsing_platform.schema_contracts import (
    SchemaContractRegistry,
    build_default_schema_contract_registry,
)


class ParsingOrchestrationError(RuntimeError):
    """Base error for invalid parsing profiles and execution plans."""


class InvalidProfileError(ParsingOrchestrationError):
    """Raised when a project parsing profile is structurally invalid."""


class MissingCapabilityError(ParsingOrchestrationError):
    """Raised when no selected module or initial input provides a requirement."""

    def __init__(self, missing: Mapping[str, Sequence[str]]):
        self.missing = {module: tuple(values) for module, values in missing.items()}
        details = "; ".join(
            f"{module}: {', '.join(capabilities)}"
            for module, capabilities in sorted(self.missing.items())
        )
        super().__init__(f"Missing required parsing capabilities: {details}")


class DependencyCycleError(ParsingOrchestrationError):
    """Raised when selected module requirements form a cycle."""

    def __init__(self, module_ids: Sequence[str]):
        self.module_ids = tuple(module_ids)
        super().__init__(
            "Parsing module dependency graph contains a cycle: " + ", ".join(self.module_ids)
        )


class InvalidModuleResultError(ParsingOrchestrationError):
    """Raised when a module returns neither a mapping nor a model-like value."""


def _as_mapping(value: Any, *, label: str, allow_none: bool = False) -> dict[str, Any]:
    if value is None and allow_none:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(by_alias=False, mode="python")
        except TypeError:
            dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dict(dumped)
    legacy_dict = getattr(value, "dict", None)
    if callable(legacy_dict):
        dumped = legacy_dict()
        if isinstance(dumped, Mapping):
            return dict(dumped)
    if is_dataclass(value) and not isinstance(value, type):
        dumped = asdict(value)
        if isinstance(dumped, Mapping):
            return dict(dumped)
    raise TypeError(f"{label} must be a mapping or expose model_dump()/dict()")


def _field(data: Mapping[str, Any], snake: str, camel: str | None = None, default: Any = None) -> Any:
    if snake in data:
        return data[snake]
    if camel and camel in data:
        return data[camel]
    return default


def _strings(value: Any, *, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values: Sequence[Any] = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = value
    elif isinstance(value, (set, frozenset)):
        values = sorted(value, key=str)
    else:
        raise InvalidProfileError(f"{label} must be a string collection")
    result: list[str] = []
    for raw in values:
        item = str(raw).strip()
        if not item:
            raise InvalidProfileError(f"{label} cannot contain an empty value")
        if item not in result:
            result.append(item)
    return tuple(result)


def _profile_id(value: Any, *, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise InvalidProfileError(f"{label} is required")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", normalized):
        raise InvalidProfileError(f"Invalid {label}: {normalized}")
    return normalized


def _failure_policy(value: Any, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    normalized = str(value or "fail_closed").strip().lower()
    if normalized not in FAILURE_POLICIES:
        raise InvalidProfileError(
            f"failure_policy must be one of: {', '.join(sorted(FAILURE_POLICIES))}"
        )
    return normalized


@dataclass(frozen=True)
class ParsingStageProfile:
    stage_id: str
    module_refs: tuple[str, ...]
    failure_policy: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage_id", _profile_id(self.stage_id, label="stage_id"))
        object.__setattr__(self, "module_refs", _strings(self.module_refs, label="module_refs"))
        if not self.module_refs:
            raise InvalidProfileError(f"Stage {self.stage_id} must contain at least one module")
        object.__setattr__(
            self,
            "failure_policy",
            _failure_policy(self.failure_policy, allow_none=True),
        )

    @classmethod
    def from_value(cls, value: Any) -> "ParsingStageProfile":
        if isinstance(value, cls):
            return value
        data = _as_mapping(value, label="ordered stage")
        return cls(
            stage_id=_field(data, "stage_id", "stageId", ""),
            module_refs=_field(
                data,
                "module_refs",
                "moduleRefs",
                _field(data, "module_ids", "moduleIds", ()),
            ),
            failure_policy=_field(data, "failure_policy", "failurePolicy"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "module_refs": list(self.module_refs),
            "failure_policy": self.failure_policy,
        }


@dataclass(frozen=True)
class ProjectParsingProfile:
    """Versioned composition policy for one project or project archetype."""

    profile_id: str
    version: str
    document_taxonomy_version: str
    selected_modules: tuple[str, ...] = ()
    excluded_modules: tuple[str, ...] = ()
    ordered_stages: tuple[ParsingStageProfile, ...] = ()
    failure_policy: str = "fail_closed"
    project_id: str | None = None
    document_types: tuple[str, ...] = ()
    asset_types: tuple[str, ...] = ()
    deal_types: tuple[str, ...] = ()
    capability_bindings: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict, compare=False, hash=False
    )
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", _profile_id(self.profile_id, label="profile_id"))
        version = str(self.version or "").strip()
        if not version:
            raise InvalidProfileError("profile version is required")
        object.__setattr__(self, "version", version)
        taxonomy_version = str(self.document_taxonomy_version or "").strip()
        if not taxonomy_version:
            raise InvalidProfileError(
                "document_taxonomy_version is required for every parsing profile"
            )
        if taxonomy_version != DOCUMENT_TAXONOMY_VERSION:
            raise InvalidProfileError(
                "Unsupported document taxonomy version: "
                f"{taxonomy_version}; expected {DOCUMENT_TAXONOMY_VERSION}"
            )
        object.__setattr__(
            self,
            "document_taxonomy_version",
            taxonomy_version,
        )
        object.__setattr__(
            self, "selected_modules", _strings(self.selected_modules, label="selected_modules")
        )
        object.__setattr__(
            self, "excluded_modules", _strings(self.excluded_modules, label="excluded_modules")
        )
        object.__setattr__(
            self,
            "ordered_stages",
            tuple(ParsingStageProfile.from_value(item) for item in self.ordered_stages),
        )
        stage_ids = [stage.stage_id for stage in self.ordered_stages]
        if len(stage_ids) != len(set(stage_ids)):
            raise InvalidProfileError("ordered_stages cannot contain duplicate stage IDs")
        object.__setattr__(self, "failure_policy", _failure_policy(self.failure_policy))
        object.__setattr__(self, "project_id", str(self.project_id) if self.project_id else None)
        raw_document_types = _strings(
            self.document_types,
            label="document_types",
        )
        if not raw_document_types:
            raise InvalidProfileError(
                "document_types must list at least one canonical taxonomy type"
            )
        try:
            document_types = canonical_document_types(raw_document_types)
        except ValueError as exc:
            raise InvalidProfileError(str(exc)) from exc
        object.__setattr__(self, "document_types", document_types)
        for name in ("asset_types", "deal_types"):
            object.__setattr__(self, name, _strings(getattr(self, name), label=name))
        normalized_bindings: dict[str, tuple[str, ...]] = {}
        for capability, module_refs in dict(self.capability_bindings or {}).items():
            capability_key = str(capability or "").strip()
            if not capability_key:
                raise InvalidProfileError("capability_bindings cannot contain an empty key")
            refs = _strings(module_refs, label=f"capability_bindings[{capability_key}]")
            if not refs:
                raise InvalidProfileError(
                    f"capability_bindings[{capability_key}] must name at least one provider"
                )
            normalized_bindings[capability_key] = refs
        object.__setattr__(self, "capability_bindings", normalized_bindings)
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    @classmethod
    def from_value(cls, value: Any) -> "ProjectParsingProfile":
        if isinstance(value, cls):
            return value
        data = _as_mapping(value, label="project parsing profile")
        raw_stages = _field(data, "ordered_stages", "orderedStages", ()) or ()
        return cls(
            profile_id=_field(data, "profile_id", "profileId", ""),
            version=_field(data, "version", default=""),
            document_taxonomy_version=_field(
                data,
                "document_taxonomy_version",
                "documentTaxonomyVersion",
                None,
            ),
            selected_modules=_field(data, "selected_modules", "selectedModules", ()),
            excluded_modules=_field(data, "excluded_modules", "excludedModules", ()),
            ordered_stages=tuple(ParsingStageProfile.from_value(item) for item in raw_stages),
            failure_policy=_field(data, "failure_policy", "failurePolicy", "fail_closed"),
            project_id=_field(data, "project_id", "projectId"),
            document_types=_field(data, "document_types", "documentTypes", ()),
            asset_types=_field(data, "asset_types", "assetTypes", ()),
            deal_types=_field(data, "deal_types", "dealTypes", ()),
            capability_bindings=_field(
                data, "capability_bindings", "capabilityBindings", {}
            )
            or {},
            metadata=_field(data, "metadata", default={}) or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "version": self.version,
            "document_taxonomy_version": self.document_taxonomy_version,
            "selected_modules": list(self.selected_modules),
            "excluded_modules": list(self.excluded_modules),
            "ordered_stages": [stage.to_dict() for stage in self.ordered_stages],
            "failure_policy": self.failure_policy,
            "project_id": self.project_id,
            "document_types": list(self.document_types),
            "asset_types": list(self.asset_types),
            "deal_types": list(self.deal_types),
            "capability_bindings": {
                capability: list(module_refs)
                for capability, module_refs in sorted(self.capability_bindings.items())
            },
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ResolvedParsingModule:
    module: ParsingModule = field(repr=False, compare=False)
    manifest: ParserModuleManifest
    stage_id: str
    stage_index: int
    profile_order: int

    @property
    def module_id(self) -> str:
        return self.manifest.module_id


@dataclass(frozen=True)
class ResolvedParsingStage:
    stage_id: str
    modules: tuple[ResolvedParsingModule, ...]
    failure_policy: str


@dataclass(frozen=True)
class ParsingExecutionPlan:
    profile: ProjectParsingProfile
    stages: tuple[ResolvedParsingStage, ...]
    base_capabilities: tuple[str, ...]
    dependencies: Mapping[str, tuple[str, ...]]

    @property
    def modules(self) -> tuple[ResolvedParsingModule, ...]:
        return tuple(module for stage in self.stages for module in stage.modules)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "base_capabilities": list(self.base_capabilities),
            "dependencies": {
                module_id: list(dependencies)
                for module_id, dependencies in sorted(self.dependencies.items())
            },
            "stages": [
                {
                    "stage_id": stage.stage_id,
                    "failure_policy": stage.failure_policy,
                    "modules": [module.manifest.to_dict() for module in stage.modules],
                }
                for stage in self.stages
            ],
        }


@dataclass(frozen=True)
class ModuleExecutionResult:
    module_id: str
    module_version: str
    stage_id: str
    execution_status: str
    idempotency_key: str
    output: Mapping[str, Any] | None = None
    output_hash: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    missing_capabilities: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "module_version": self.module_version,
            "stage_id": self.stage_id,
            "execution_status": self.execution_status,
            "idempotency_key": self.idempotency_key,
            "output": dict(self.output) if self.output is not None else None,
            "output_hash": self.output_hash,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "missing_capabilities": list(self.missing_capabilities),
        }


@dataclass(frozen=True)
class ModuleExecutionTrace:
    module_id: str
    module_version: str
    stage_id: str
    execution_status: str
    idempotency_key: str
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    required_capabilities: tuple[str, ...]
    provided_capabilities: tuple[str, ...]
    output_hash: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "module_version": self.module_version,
            "stage_id": self.stage_id,
            "execution_status": self.execution_status,
            "idempotency_key": self.idempotency_key,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_ms": self.duration_ms,
            "required_capabilities": list(self.required_capabilities),
            "provided_capabilities": list(self.provided_capabilities),
            "output_hash": self.output_hash,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class ParsingOrchestrationResult:
    execution_status: str
    idempotency_key: str
    profile_id: str
    profile_version: str
    results: Mapping[str, ModuleExecutionResult]
    trace: tuple[ModuleExecutionTrace, ...]
    available_capabilities: tuple[str, ...]
    failed_module_ids: tuple[str, ...] = ()
    skipped_module_ids: tuple[str, ...] = ()
    approval_status: str = "not_requested"

    @property
    def outputs(self) -> dict[str, Mapping[str, Any]]:
        return {
            module_id: result.output
            for module_id, result in self.results.items()
            if result.execution_status == "completed" and result.output is not None
        }

    @property
    def authoritative(self) -> bool:
        # Parser execution success is never equivalent to business approval.
        # An approval service must create a separate approved snapshot before
        # any parsed candidate can become an authoritative project value.
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_status": self.execution_status,
            "approval_status": self.approval_status,
            "authoritative": self.authoritative,
            "idempotency_key": self.idempotency_key,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "results": {key: value.to_dict() for key, value in self.results.items()},
            "outputs": {key: dict(value) for key, value in self.outputs.items()},
            "trace": [item.to_dict() for item in self.trace],
            "available_capabilities": list(self.available_capabilities),
            "failed_module_ids": list(self.failed_module_ids),
            "skipped_module_ids": list(self.skipped_module_ids),
        }


class ParsingOrchestrator:
    """Compose registered parsing modules into deterministic project pipelines."""

    def __init__(
        self,
        registry: ParserModuleRegistry,
        schema_registry: SchemaContractRegistry | None = None,
    ) -> None:
        self.registry = registry
        self.schema_registry = schema_registry or build_default_schema_contract_registry()

    def build_plan(
        self,
        profile: ProjectParsingProfile | Mapping[str, Any] | Any,
        *,
        base_capabilities: Sequence[str] = (),
    ) -> ParsingExecutionPlan:
        project_profile = ProjectParsingProfile.from_value(profile)
        base = _strings(base_capabilities, label="base_capabilities")
        stage_specs = self._active_stages(project_profile)

        resolved: list[ResolvedParsingModule] = []
        module_ids: set[str] = set()
        order = 0
        for stage_index, stage in enumerate(stage_specs):
            for module_ref in stage.module_refs:
                try:
                    module = self.registry.resolve(module_ref)
                except UnknownModuleError as exc:
                    raise InvalidProfileError(str(exc)) from exc
                manifest = self.registry.manifest_for(module)
                # Schema ownership is resolved during planning so an explicitly
                # composed but unregistered contract can never reach execution.
                self.schema_registry.require(manifest.input_schema)
                self.schema_registry.require(manifest.output_schema)
                if not _manifest_matches_profile(manifest, project_profile):
                    raise InvalidProfileError(
                        f"Module {manifest.ref} is not compatible with profile "
                        f"{project_profile.profile_id}@{project_profile.version}"
                    )
                if manifest.module_id in module_ids:
                    raise InvalidProfileError(
                        f"Profile selects module ID more than once: {manifest.module_id}"
                    )
                module_ids.add(manifest.module_id)
                resolved.append(
                    ResolvedParsingModule(
                        module=module,
                        manifest=manifest,
                        stage_id=stage.stage_id,
                        stage_index=stage_index,
                        profile_order=order,
                    )
                )
                order += 1

        missing: dict[str, list[str]] = {}
        provider_by_capability: dict[str, list[ResolvedParsingModule]] = {}
        for entry in resolved:
            for capability in (
                *entry.manifest.provides,
                *entry.manifest.conditional_provides,
            ):
                provider_by_capability.setdefault(capability, []).append(entry)
        for entry in resolved:
            absent = [
                capability
                for capability in entry.manifest.requires
                if capability not in base and capability not in provider_by_capability
            ]
            if absent:
                missing[entry.module_id] = absent
        if missing:
            raise MissingCapabilityError(missing)

        # Stage ordering is a hard barrier. Capability edges refine order within
        # a stage and expose cycles caused by invalid producer/consumer layouts.
        edges: dict[str, set[str]] = {entry.module_id: set() for entry in resolved}
        indegree: dict[str, int] = {entry.module_id: 0 for entry in resolved}
        by_id = {entry.module_id: entry for entry in resolved}

        def add_edge(source: str, target: str) -> None:
            if source == target or target in edges[source]:
                return
            edges[source].add(target)
            indegree[target] += 1

        for source in resolved:
            for target in resolved:
                if source.stage_index < target.stage_index:
                    add_edge(source.module_id, target.module_id)

        dependencies: dict[str, set[str]] = {entry.module_id: set() for entry in resolved}
        for consumer in resolved:
            for capability in consumer.manifest.requires:
                if capability in base:
                    continue
                providers = provider_by_capability[capability]
                eligible = [
                    provider
                    for provider in providers
                    if provider.module_id != consumer.module_id
                    and provider.stage_index <= consumer.stage_index
                ]
                if not eligible:
                    missing.setdefault(consumer.module_id, []).append(capability)
                    continue
                bindings = project_profile.capability_bindings.get(capability)
                if bindings:
                    candidates = []
                    missing_bindings: list[str] = []
                    for binding in bindings:
                        binding_id, binding_version = split_module_ref(binding)
                        matched = [
                            provider
                            for provider in eligible
                            if provider.module_id == binding_id
                            and (
                                binding_version is None
                                or provider.manifest.version == binding_version
                            )
                        ]
                        if not matched:
                            missing_bindings.append(binding)
                        else:
                            candidates.extend(matched)
                    if missing_bindings:
                        raise InvalidProfileError(
                            f"Capability binding for {capability} references an unavailable "
                            f"provider for {consumer.module_id}: {', '.join(missing_bindings)}"
                        )
                else:
                    # Preserve every eligible provider.  Fallback/ensemble output
                    # must be visible to an explicit reconciliation consumer; the
                    # orchestrator must never silently select the first provider.
                    candidates = eligible
                for provider in sorted(
                    candidates, key=lambda item: (item.stage_index, item.profile_order)
                ):
                    add_edge(provider.module_id, consumer.module_id)
                    dependencies[consumer.module_id].add(provider.module_id)
        if missing:
            raise MissingCapabilityError(missing)

        ready = sorted(
            (entry for entry in resolved if indegree[entry.module_id] == 0),
            key=lambda item: item.profile_order,
        )
        ordered: list[ResolvedParsingModule] = []
        while ready:
            current = ready.pop(0)
            ordered.append(current)
            for target_id in sorted(
                edges[current.module_id], key=lambda module_id: by_id[module_id].profile_order
            ):
                indegree[target_id] -= 1
                if indegree[target_id] == 0:
                    ready.append(by_id[target_id])
                    ready.sort(key=lambda item: item.profile_order)
        if len(ordered) != len(resolved):
            unresolved = [
                entry.module_id for entry in resolved if indegree[entry.module_id] > 0
            ]
            raise DependencyCycleError(unresolved)

        resolved_stages = tuple(
            ResolvedParsingStage(
                stage_id=stage.stage_id,
                modules=tuple(entry for entry in ordered if entry.stage_id == stage.stage_id),
                failure_policy=stage.failure_policy or project_profile.failure_policy,
            )
            for stage in stage_specs
        )
        return ParsingExecutionPlan(
            profile=project_profile,
            stages=resolved_stages,
            base_capabilities=base,
            dependencies={key: tuple(sorted(values)) for key, values in dependencies.items()},
        )

    async def execute(
        self,
        profile: ProjectParsingProfile | Mapping[str, Any] | Any,
        payload: Mapping[str, Any] | Any,
        *,
        context: Mapping[str, Any] | Any | None = None,
        base_capabilities: Sequence[str] = (),
    ) -> ParsingOrchestrationResult:
        plan = self.build_plan(profile, base_capabilities=base_capabilities)
        input_payload = _as_mapping(payload, label="parsing payload")
        execution_context = _as_mapping(context, label="execution context", allow_none=True)
        key = build_idempotency_key(plan, input_payload)
        return await self._execute_stages(
            plan=plan,
            stages=plan.stages,
            payload=input_payload,
            context=execution_context,
            idempotency_key=key,
            initial_results={},
            initial_capabilities=set(plan.base_capabilities),
        )

    async def execute_stage(
        self,
        profile: ProjectParsingProfile | Mapping[str, Any] | Any,
        stage_id: str,
        payload: Mapping[str, Any] | Any,
        *,
        context: Mapping[str, Any] | Any | None = None,
        upstream_results: Mapping[str, Any] | Any | None = None,
        available_capabilities: Sequence[str] = (),
    ) -> ParsingOrchestrationResult:
        """Execute one stage independently with explicit upstream state."""

        plan = self.build_plan(profile, base_capabilities=available_capabilities)
        matches = tuple(stage for stage in plan.stages if stage.stage_id == stage_id)
        if not matches:
            raise InvalidProfileError(f"Unknown parsing stage in profile: {stage_id}")
        input_payload = _as_mapping(payload, label="parsing payload")
        execution_context = _as_mapping(context, label="execution context", allow_none=True)
        initial_results = _normalize_upstream_results(upstream_results)
        key = build_idempotency_key(plan, input_payload, scope=f"stage:{stage_id}")
        return await self._execute_stages(
            plan=plan,
            stages=matches,
            payload=input_payload,
            context=execution_context,
            idempotency_key=key,
            initial_results=initial_results,
            initial_capabilities=set(plan.base_capabilities),
        )

    def execute_sync(self, *args: Any, **kwargs: Any) -> ParsingOrchestrationResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.execute(*args, **kwargs))
        raise RuntimeError("execute_sync cannot run inside an active event loop; await execute()")

    def execute_stage_sync(self, *args: Any, **kwargs: Any) -> ParsingOrchestrationResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.execute_stage(*args, **kwargs))
        raise RuntimeError(
            "execute_stage_sync cannot run inside an active event loop; await execute_stage()"
        )

    async def _execute_stages(
        self,
        *,
        plan: ParsingExecutionPlan,
        stages: Sequence[ResolvedParsingStage],
        payload: dict[str, Any],
        context: dict[str, Any],
        idempotency_key: str,
        initial_results: dict[str, ModuleExecutionResult],
        initial_capabilities: set[str],
    ) -> ParsingOrchestrationResult:
        _validate_execution_scope(plan.profile, payload, context)
        results = dict(initial_results)
        trace: list[ModuleExecutionTrace] = []
        capabilities = set(initial_capabilities)
        failed: list[str] = []
        skipped: list[str] = []
        abort = False

        selected_entries = [entry for stage in stages for entry in stage.modules]
        for stage in stages:
            for entry in stage.modules:
                if abort:
                    self._record_skip(
                        entry=entry,
                        idempotency_key=idempotency_key,
                        results=results,
                        trace=trace,
                        skipped=skipped,
                        error_code="UPSTREAM_FAIL_CLOSED",
                        error_message="A prior module failed under fail_closed policy",
                    )
                    continue
                missing_runtime = tuple(
                    capability
                    for capability in entry.manifest.requires
                    if capability not in capabilities
                )
                if missing_runtime:
                    self._record_skip(
                        entry=entry,
                        idempotency_key=idempotency_key,
                        results=results,
                        trace=trace,
                        skipped=skipped,
                        error_code="RUNTIME_CAPABILITY_UNAVAILABLE",
                        error_message=(
                            "Required capabilities were not produced successfully: "
                            + ", ".join(missing_runtime)
                        ),
                        missing_capabilities=missing_runtime,
                    )
                    if stage.failure_policy == "fail_closed":
                        abort = True
                    continue

                module_key = _module_idempotency_key(idempotency_key, entry)
                started = datetime.now(timezone.utc)
                dependency_ids = set(plan.dependencies.get(entry.module_id, ()))
                module_payload = {
                    "input": dict(payload),
                    "module_results": {
                        module_id: result.to_dict()
                        for module_id, result in results.items()
                        if module_id in dependency_ids
                    },
                    "available_capabilities": sorted(
                        capabilities.intersection(entry.manifest.requires)
                    ),
                }
                effective_project_id = plan.profile.project_id or context.get("project_id")
                module_context = {
                    **context,
                    "profile_id": plan.profile.profile_id,
                    "profile_version": plan.profile.version,
                    "project_id": effective_project_id,
                    "stage_id": entry.stage_id,
                    "module_id": entry.module_id,
                    "module_version": entry.manifest.version,
                    "orchestration_idempotency_key": idempotency_key,
                    "idempotency_key": module_key,
                }
                try:
                    self.schema_registry.validate(
                        entry.manifest.input_schema,
                        module_payload,
                        boundary="input",
                        module_ref=entry.manifest.ref,
                    )
                    raw = entry.module.execute(module_payload, module_context)
                    if inspect.isawaitable(raw):
                        raw = await raw
                    output = _validate_module_output(entry, raw)
                    self.schema_registry.validate(
                        entry.manifest.output_schema,
                        output,
                        boundary="output",
                        module_ref=entry.manifest.ref,
                    )
                except Exception as exc:  # module boundary: failures become trace data
                    finished = datetime.now(timezone.utc)
                    code, message = _public_module_error(exc)
                    result = ModuleExecutionResult(
                        module_id=entry.module_id,
                        module_version=entry.manifest.version,
                        stage_id=entry.stage_id,
                        execution_status="failed",
                        idempotency_key=module_key,
                        error_code=code,
                        error_message=message,
                    )
                    results[entry.module_id] = result
                    failed.append(entry.module_id)
                    trace.append(
                        _trace(
                            entry=entry,
                            execution_status="failed",
                            idempotency_key=module_key,
                            started=started,
                            finished=finished,
                            error_code=code,
                            error_message=message,
                        )
                    )
                    if stage.failure_policy == "fail_closed":
                        abort = True
                    continue

                finished = datetime.now(timezone.utc)
                output_hash = _canonical_output_hash(output)
                conditional_capabilities = _conditional_capabilities(
                    entry.manifest, output
                )
                provided_capabilities = tuple(
                    dict.fromkeys(
                        (*entry.manifest.provides, *conditional_capabilities)
                    )
                )
                result = ModuleExecutionResult(
                    module_id=entry.module_id,
                    module_version=entry.manifest.version,
                    stage_id=entry.stage_id,
                    execution_status="completed",
                    idempotency_key=module_key,
                    output=output,
                    output_hash=output_hash,
                )
                results[entry.module_id] = result
                capabilities.update(provided_capabilities)
                trace.append(
                    _trace(
                        entry=entry,
                        execution_status="completed",
                        idempotency_key=module_key,
                        started=started,
                        finished=finished,
                        provided_capabilities=provided_capabilities,
                        output_hash=output_hash,
                    )
                )

        # If fail_closed aborted a single-stage selection, entries in a later
        # selected stage are absent from the loop only when callers passed a
        # filtered stage sequence. Full executions always include every entry.
        executed_ids = set(results).intersection(entry.module_id for entry in selected_entries)
        for entry in selected_entries:
            if entry.module_id not in executed_ids:
                self._record_skip(
                    entry=entry,
                    idempotency_key=idempotency_key,
                    results=results,
                    trace=trace,
                    skipped=skipped,
                    error_code="NOT_EXECUTED",
                    error_message="Module was not reached",
                )

        if abort:
            status = "failed"
        elif failed or skipped:
            status = "partial"
        else:
            status = "completed"
        return ParsingOrchestrationResult(
            execution_status=status,
            idempotency_key=idempotency_key,
            profile_id=plan.profile.profile_id,
            profile_version=plan.profile.version,
            results=results,
            trace=tuple(trace),
            available_capabilities=tuple(sorted(capabilities)),
            failed_module_ids=tuple(failed),
            skipped_module_ids=tuple(skipped),
        )

    def _record_skip(
        self,
        *,
        entry: ResolvedParsingModule,
        idempotency_key: str,
        results: dict[str, ModuleExecutionResult],
        trace: list[ModuleExecutionTrace],
        skipped: list[str],
        error_code: str,
        error_message: str,
        missing_capabilities: tuple[str, ...] = (),
    ) -> None:
        module_key = _module_idempotency_key(idempotency_key, entry)
        now = datetime.now(timezone.utc)
        results[entry.module_id] = ModuleExecutionResult(
            module_id=entry.module_id,
            module_version=entry.manifest.version,
            stage_id=entry.stage_id,
            execution_status="skipped",
            idempotency_key=module_key,
            error_code=error_code,
            error_message=error_message,
            missing_capabilities=missing_capabilities,
        )
        skipped.append(entry.module_id)
        trace.append(
            _trace(
                entry=entry,
                execution_status="skipped",
                idempotency_key=module_key,
                started=now,
                finished=now,
                error_code=error_code,
                error_message=error_message,
            )
        )

    def _active_stages(self, profile: ProjectParsingProfile) -> tuple[ParsingStageProfile, ...]:
        selected = tuple(profile.selected_modules)
        excluded = tuple(profile.excluded_modules)

        def is_excluded(module_ref: str) -> bool:
            module_id, version = split_module_ref(module_ref)
            for excluded_ref in excluded:
                excluded_id, excluded_version = split_module_ref(excluded_ref)
                if module_id == excluded_id and (
                    excluded_version is None or excluded_version == version
                ):
                    return True
            return False

        def selected_reference(module_ref: str) -> str | None:
            if not selected:
                return module_ref
            module_id, version = split_module_ref(module_ref)
            for selected_ref in selected:
                selected_id, selected_version = split_module_ref(selected_ref)
                if module_id == selected_id and (
                    selected_version is None or version is None or selected_version == version
                ):
                    # A project-level version pin must win when the stage only
                    # names a module ID; otherwise a newly registered release
                    # could silently change an existing project's semantics.
                    if selected_version is not None and version is None:
                        return selected_ref
                    return module_ref
            return None

        if profile.ordered_stages:
            stages: list[ParsingStageProfile] = []
            assigned_ids: set[str] = set()
            for stage in profile.ordered_stages:
                refs_list: list[str] = []
                for module_ref in stage.module_refs:
                    active_ref = selected_reference(module_ref)
                    if active_ref is not None and not is_excluded(active_ref):
                        refs_list.append(active_ref)
                refs = tuple(refs_list)
                if not refs:
                    continue
                assigned_ids.update(split_module_ref(module_ref)[0] for module_ref in refs)
                stages.append(
                    ParsingStageProfile(
                        stage_id=stage.stage_id,
                        module_refs=refs,
                        failure_policy=stage.failure_policy,
                    )
                )
            selected_active_ids = {
                split_module_ref(module_ref)[0]
                for module_ref in selected
                if not is_excluded(module_ref)
            }
            unassigned = sorted(selected_active_ids.difference(assigned_ids))
            if unassigned:
                raise InvalidProfileError(
                    "Selected modules are not assigned to an ordered stage: "
                    + ", ".join(unassigned)
                )
            if not stages:
                raise InvalidProfileError("Project parsing profile selects no executable modules")
            return tuple(stages)

        refs = tuple(module_ref for module_ref in selected if not is_excluded(module_ref))
        if not refs:
            # With no explicit composition, use all active compatible modules.
            refs = tuple(
                manifest.ref
                for manifest in self.registry.list_active()
                if not is_excluded(manifest.ref)
                and _manifest_matches_profile(manifest, profile)
            )
        if not refs:
            raise InvalidProfileError("Project parsing profile selects no executable modules")
        return (ParsingStageProfile(stage_id="default", module_refs=refs),)


def _manifest_matches_profile(
    manifest: ParserModuleManifest,
    profile: ProjectParsingProfile,
) -> bool:
    return (
        manifest.document_taxonomy_version
        == profile.document_taxonomy_version
        and _supports_document_types(
            manifest.supported_document_types,
            profile.document_types,
            scope=manifest.document_type_scope,
        )
        and _supports_any(manifest.supported_asset_types, profile.asset_types)
        and _supports_any(manifest.supported_deal_types, profile.deal_types)
    )


def _supports_document_types(
    supported: Sequence[str],
    requested: Sequence[str],
    *,
    scope: str,
) -> bool:
    if not requested:
        return False
    if scope == DOCUMENT_TYPE_SCOPE_ALL:
        return True
    if scope != DOCUMENT_TYPE_SCOPE_LISTED or not supported:
        return False
    supported_keys = {value.casefold() for value in supported}
    requested_keys = {
        canonical_document_type(value)
        for value in requested
    }
    return bool(requested_keys.intersection(supported_keys))


def _supports_any(
    supported: Sequence[str],
    requested: Sequence[str],
) -> bool:
    if not requested or not supported:
        return True
    supported_keys = {value.casefold() for value in supported}
    requested_keys = {value.casefold() for value in requested}
    return bool(requested_keys.intersection(supported_keys))


def _normalize_upstream_results(value: Any) -> dict[str, ModuleExecutionResult]:
    if value is None:
        return {}
    raw = _as_mapping(value, label="upstream_results")
    normalized: dict[str, ModuleExecutionResult] = {}
    for module_id, output in raw.items():
        if isinstance(output, ModuleExecutionResult):
            normalized[str(module_id)] = output
            continue
        output_mapping = _as_mapping(output, label=f"upstream result {module_id}")
        normalized[str(module_id)] = ModuleExecutionResult(
            module_id=str(module_id),
            module_version=str(output_mapping.get("module_version") or "upstream"),
            stage_id=str(output_mapping.get("stage_id") or "upstream"),
            execution_status=str(
                output_mapping.get("execution_status")
                or output_mapping.get("executionStatus")
                or output_mapping.get("status")
                or "completed"
            ),
            idempotency_key=str(output_mapping.get("idempotency_key") or "upstream"),
            output=_as_mapping(
                output_mapping.get("output", output_mapping),
                label=f"upstream output {module_id}",
            ),
            output_hash=(
                str(output_mapping.get("output_hash") or output_mapping.get("outputHash"))
                if output_mapping.get("output_hash") or output_mapping.get("outputHash")
                else _canonical_output_hash(
                    _as_mapping(
                        output_mapping.get("output", output_mapping),
                        label=f"upstream output {module_id}",
                    )
                )
            ),
        )
    return normalized


def _trace(
    *,
    entry: ResolvedParsingModule,
    execution_status: str,
    idempotency_key: str,
    started: datetime,
    finished: datetime,
    provided_capabilities: tuple[str, ...] | None = None,
    output_hash: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> ModuleExecutionTrace:
    return ModuleExecutionTrace(
        module_id=entry.module_id,
        module_version=entry.manifest.version,
        stage_id=entry.stage_id,
        execution_status=execution_status,
        idempotency_key=idempotency_key,
        started_at=started,
        finished_at=finished,
        duration_ms=max(0.0, (finished - started).total_seconds() * 1000),
        required_capabilities=entry.manifest.requires,
        provided_capabilities=(
            (provided_capabilities or entry.manifest.provides)
            if execution_status == "completed"
            else ()
        ),
        output_hash=output_hash,
        error_code=error_code,
        error_message=error_message,
    )


def _canonical_output_hash(output: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(output).encode("utf-8")).hexdigest()


def _validate_execution_scope(
    profile: ProjectParsingProfile,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> None:
    document = payload.get("document")
    document_project_id = getattr(document, "project_id", None)
    document_tenant_id = getattr(document, "tenant_id", None)
    project_scopes = {
        str(value)
        for value in (profile.project_id, context.get("project_id"), document_project_id)
        if value is not None and str(value).strip()
    }
    if len(project_scopes) > 1:
        raise InvalidProfileError(
            "Parsing profile, execution context, and document project scopes do not match"
        )
    tenant_scopes = {
        str(value)
        for value in (context.get("tenant_id"), document_tenant_id)
        if value is not None and str(value).strip()
    }
    if len(tenant_scopes) > 1:
        raise InvalidProfileError(
            "Execution context and document tenant scopes do not match"
        )


def _validate_module_output(
    entry: ResolvedParsingModule,
    raw: Any,
) -> dict[str, Any]:
    try:
        output = _as_mapping(raw, label=f"result from {entry.module_id}")
    except TypeError as exc:
        raise InvalidModuleResultError(
            f"Module {entry.manifest.ref} must return a mapping or model-like value"
        ) from exc

    validator = getattr(entry.module, "validate_output", None)
    if callable(validator):
        validated = validator(dict(output))
        if validated is not None:
            try:
                output = _as_mapping(
                    validated, label=f"validated result from {entry.module_id}"
                )
            except TypeError as exc:
                raise InvalidModuleResultError(
                    f"Module {entry.manifest.ref} output validator returned an invalid value"
                ) from exc
    try:
        canonical = _canonicalize(output)
        encoded = json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise InvalidModuleResultError(
            f"Module {entry.manifest.ref} output is not a safe JSON transport"
        ) from exc
    if not isinstance(canonical, dict):
        raise InvalidModuleResultError(
            f"Module {entry.manifest.ref} output must resolve to an object"
        )
    if len(encoded) > entry.manifest.max_output_bytes:
        raise InvalidModuleResultError(
            f"Module {entry.manifest.ref} output exceeds its declared size limit"
        )
    return canonical


def _conditional_capabilities(
    manifest: ParserModuleManifest,
    output: Mapping[str, Any],
) -> tuple[str, ...]:
    raw = output.get("providedCapabilities", output.get("provided_capabilities", ()))
    if raw is None:
        return ()
    if isinstance(raw, str):
        requested = (raw,)
    elif isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
        requested = tuple(str(item) for item in raw)
    else:
        raise InvalidModuleResultError(
            f"Module {manifest.ref} providedCapabilities must be a string collection"
        )
    unknown = sorted(set(requested).difference(manifest.conditional_provides))
    if unknown:
        raise InvalidModuleResultError(
            f"Module {manifest.ref} emitted undeclared conditional capabilities: "
            + ", ".join(unknown)
        )
    return tuple(dict.fromkeys(requested))


def _public_module_error(exc: Exception) -> tuple[str, str]:
    raw_code = str(getattr(exc, "code", None) or type(exc).__name__)
    code = re.sub(r"[^A-Za-z0-9_.:-]", "_", raw_code)[:96] or "MODULE_EXECUTION_FAILED"
    explicit_message = getattr(exc, "public_message", None)
    if isinstance(explicit_message, str) and explicit_message.strip():
        message = explicit_message.strip()[:512]
    elif isinstance(exc, InvalidModuleResultError):
        message = "Module output violated its declared transport contract."
    else:
        message = "Module execution failed at a controlled boundary."
    return code, message


def build_idempotency_key(
    plan: ParsingExecutionPlan,
    payload: Mapping[str, Any] | Any,
    *,
    scope: str = "pipeline",
) -> str:
    """Hash every execution-semantic input, excluding timestamps and trace IDs."""

    plain_payload = _as_mapping(payload, label="parsing payload")
    material = {
        "contract": "realytics-parsing-orchestration-v1",
        "scope": scope,
        "profile": plan.profile.to_dict(),
        "base_capabilities": list(plan.base_capabilities),
        "stages": [
            {
                "stage_id": stage.stage_id,
                "failure_policy": stage.failure_policy,
                "modules": [module.manifest.to_dict() for module in stage.modules],
            }
            for stage in plan.stages
        ],
        "payload": plain_payload,
    }
    digest = hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()
    return f"parsing:{digest}"


def build_plan_hash(plan: ParsingExecutionPlan) -> str:
    """Hash the pinned execution semantics stored with a ParsingRun."""

    return hashlib.sha256(_canonical_json(plan.to_dict()).encode("utf-8")).hexdigest()


def _module_idempotency_key(
    orchestration_key: str,
    entry: ResolvedParsingModule,
) -> str:
    material = {
        "orchestration_key": orchestration_key,
        "stage_id": entry.stage_id,
        "module_id": entry.module_id,
        "module_version": entry.manifest.version,
    }
    digest = hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()
    return f"parsing-module:{digest}"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _canonicalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonicalize(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Idempotency inputs cannot contain NaN or infinity")
        return value
    if isinstance(value, Enum):
        return _canonicalize(value.value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$bytes_sha256": hashlib.sha256(bytes(value)).hexdigest()}
    if isinstance(value, Mapping):
        return {
            str(key): _canonicalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (set, frozenset)):
        canonical = [_canonicalize(item) for item in value]
        return sorted(canonical, key=_canonical_json)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonicalize(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return _canonicalize(asdict(value))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _canonicalize(model_dump(by_alias=False, mode="python"))
        except TypeError:
            return _canonicalize(model_dump())
    legacy_dict = getattr(value, "dict", None)
    if callable(legacy_dict):
        return _canonicalize(legacy_dict())
    raise TypeError(f"Unsupported idempotency input type: {type(value).__name__}")


__all__ = [
    "DependencyCycleError",
    "InvalidModuleResultError",
    "InvalidProfileError",
    "MissingCapabilityError",
    "ModuleExecutionResult",
    "ModuleExecutionTrace",
    "ParsingExecutionPlan",
    "ParsingOrchestrationError",
    "ParsingOrchestrationResult",
    "ParsingOrchestrator",
    "ParsingStageProfile",
    "ProjectParsingProfile",
    "ResolvedParsingModule",
    "ResolvedParsingStage",
    "build_idempotency_key",
    "build_plan_hash",
]
