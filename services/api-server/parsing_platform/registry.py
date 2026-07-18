from __future__ import annotations

"""Versioned registry and portable contracts for document-parsing modules.

This module deliberately depends only on the Python standard library.  Parsing
modules can therefore be registered before the wider Agent Platform contracts
are imported, while still accepting dictionaries and Pydantic-style objects.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Protocol, runtime_checkable

from parsing_platform.document_taxonomy import (
    DOCUMENT_TAXONOMY_VERSION,
    DOCUMENT_TYPE_SCOPE_ALL,
    DOCUMENT_TYPE_SCOPE_LISTED,
    DOCUMENT_TYPE_SCOPES,
    canonical_document_types,
)


FAILURE_POLICIES = frozenset({"fail_closed", "preserve_partial"})
EXECUTION_CLASSES = frozenset({"deterministic", "ai_assisted", "external_service"})
DATA_CLASSIFICATIONS = frozenset({"public", "internal", "confidential", "restricted"})
LOCATOR_PRECISIONS = frozenset({"none", "document", "page_or_cell", "exact"})


class ParsingRegistryError(RuntimeError):
    """Base error raised by the parsing module registry."""


class InvalidManifestError(ParsingRegistryError):
    """Raised when a module exposes an invalid manifest."""


class UnknownModuleError(ParsingRegistryError):
    """Raised when a requested module or pinned version is not registered."""


def _model_mapping(value: Any, *, label: str) -> dict[str, Any]:
    """Convert dict/dataclass/Pydantic-like values to a plain mapping."""

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
        raise InvalidManifestError(f"{label} must be a string collection")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        item = str(raw).strip()
        if not item:
            raise InvalidManifestError(f"{label} cannot contain an empty value")
        if item not in seen:
            seen.add(item)
            normalized.append(item)
    return tuple(normalized)


def _validate_identifier(value: str, *, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise InvalidManifestError(f"{label} is required")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.:-]*", normalized):
        raise InvalidManifestError(
            f"{label} must use lowercase letters, digits, '.', '_', ':' or '-': {normalized}"
        )
    return normalized


@dataclass(frozen=True)
class ParserModuleManifest:
    """Portable identity, compatibility and capability contract for a module."""

    module_id: str
    version: str
    document_taxonomy_version: str
    capabilities: tuple[str, ...] = ()
    supported_document_types: tuple[str, ...] = ()
    document_type_scope: str = DOCUMENT_TYPE_SCOPE_LISTED
    supported_asset_types: tuple[str, ...] = ()
    supported_deal_types: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    conditional_provides: tuple[str, ...] = ()
    execution_class: str = "deterministic"
    input_schema: str = "realytics-parser-module-input-v1"
    output_schema: str = "realytics-parser-module-output-v1"
    required_locator_precision: str = "none"
    data_classification: str = "confidential"
    max_output_bytes: int = 5 * 1024 * 1024
    active: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "module_id", _validate_identifier(self.module_id, label="module_id"))
        version = str(self.version or "").strip()
        if not version:
            raise InvalidManifestError("version is required")
        object.__setattr__(self, "version", version)
        for name in (
            "capabilities",
            "supported_document_types",
            "supported_asset_types",
            "supported_deal_types",
            "requires",
            "provides",
            "conditional_provides",
        ):
            object.__setattr__(self, name, _strings(getattr(self, name), label=name))
        document_type_scope = str(self.document_type_scope or "").strip().lower()
        if "*" in self.supported_document_types:
            raise InvalidManifestError(
                "Legacy '*' document support is forbidden; declare "
                "document_type_scope='all' with a taxonomy version"
            )
        if document_type_scope not in DOCUMENT_TYPE_SCOPES:
            raise InvalidManifestError(
                "document_type_scope must be one of: "
                + ", ".join(sorted(DOCUMENT_TYPE_SCOPES))
            )
        if (
            document_type_scope == DOCUMENT_TYPE_SCOPE_ALL
            and self.supported_document_types
        ):
            raise InvalidManifestError(
                "All-types modules must not also list supported_document_types"
            )
        object.__setattr__(self, "document_type_scope", document_type_scope)
        taxonomy_version = str(self.document_taxonomy_version or "").strip()
        if not taxonomy_version:
            raise InvalidManifestError(
                "document_taxonomy_version is required for every parser module"
            )
        if taxonomy_version != DOCUMENT_TAXONOMY_VERSION:
            raise InvalidManifestError(
                "Unsupported document taxonomy version: "
                f"{taxonomy_version}; expected {DOCUMENT_TAXONOMY_VERSION}"
            )
        try:
            canonical_types = canonical_document_types(
                self.supported_document_types
            )
        except ValueError as exc:
            raise InvalidManifestError(str(exc)) from exc
        object.__setattr__(
            self,
            "supported_document_types",
            canonical_types,
        )
        if (
            document_type_scope == DOCUMENT_TYPE_SCOPE_LISTED
            and not canonical_types
        ):
            raise InvalidManifestError(
                "Listed document-type scope requires at least one canonical "
                "supported document type"
            )
        object.__setattr__(
            self,
            "document_taxonomy_version",
            taxonomy_version,
        )
        execution_class = str(self.execution_class or "").strip().lower()
        if execution_class not in EXECUTION_CLASSES:
            raise InvalidManifestError(
                "execution_class must be one of: " + ", ".join(sorted(EXECUTION_CLASSES))
            )
        object.__setattr__(self, "execution_class", execution_class)
        for name in ("input_schema", "output_schema"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise InvalidManifestError(f"{name} is required")
            object.__setattr__(self, name, value)
        locator_precision = str(self.required_locator_precision or "").strip().lower()
        if locator_precision not in LOCATOR_PRECISIONS:
            raise InvalidManifestError(
                "required_locator_precision must be one of: "
                + ", ".join(sorted(LOCATOR_PRECISIONS))
            )
        object.__setattr__(self, "required_locator_precision", locator_precision)
        data_classification = str(self.data_classification or "").strip().lower()
        if data_classification not in DATA_CLASSIFICATIONS:
            raise InvalidManifestError(
                "data_classification must be one of: "
                + ", ".join(sorted(DATA_CLASSIFICATIONS))
            )
        object.__setattr__(self, "data_classification", data_classification)
        if not isinstance(self.max_output_bytes, int) or self.max_output_bytes < 1:
            raise InvalidManifestError("max_output_bytes must be a positive integer")
        object.__setattr__(self, "metadata", dict(self.metadata or {}))
        if not self.capabilities and not self.provides and not self.conditional_provides:
            raise InvalidManifestError(
                f"Module {self.module_id} must declare at least one capability or provided value"
            )
        declared_provides = set(self.provides).union(self.conditional_provides)
        overlap = set(self.requires).intersection(declared_provides)
        if overlap:
            joined = ", ".join(sorted(overlap))
            raise InvalidManifestError(
                f"Module {self.module_id} cannot both require and provide: {joined}"
            )

    @property
    def ref(self) -> str:
        return f"{self.module_id}@{self.version}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "version": self.version,
            "capabilities": list(self.capabilities),
            "supported_document_types": list(self.supported_document_types),
            "document_taxonomy_version": self.document_taxonomy_version,
            "document_type_scope": self.document_type_scope,
            "supported_asset_types": list(self.supported_asset_types),
            "supported_deal_types": list(self.supported_deal_types),
            "requires": list(self.requires),
            "provides": list(self.provides),
            "conditional_provides": list(self.conditional_provides),
            "execution_class": self.execution_class,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "required_locator_precision": self.required_locator_precision,
            "data_classification": self.data_classification,
            "max_output_bytes": self.max_output_bytes,
            "active": self.active,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_value(cls, value: Any) -> "ParserModuleManifest":
        if isinstance(value, cls):
            return value
        data = _model_mapping(value, label="manifest")
        return cls(
            module_id=_field(data, "module_id", "moduleId", ""),
            version=_field(data, "version", default=""),
            capabilities=_field(data, "capabilities", default=()),
            supported_document_types=_field(
                data, "supported_document_types", "supportedDocumentTypes", ()
            ),
            document_taxonomy_version=_field(
                data,
                "document_taxonomy_version",
                "documentTaxonomyVersion",
                None,
            ),
            document_type_scope=_field(
                data,
                "document_type_scope",
                "documentTypeScope",
                DOCUMENT_TYPE_SCOPE_LISTED,
            ),
            supported_asset_types=_field(data, "supported_asset_types", "supportedAssetTypes", ()),
            supported_deal_types=_field(data, "supported_deal_types", "supportedDealTypes", ()),
            requires=_field(data, "requires", default=()),
            provides=_field(data, "provides", default=()),
            conditional_provides=_field(
                data, "conditional_provides", "conditionalProvides", ()
            ),
            execution_class=_field(data, "execution_class", "executionClass", "deterministic"),
            input_schema=_field(
                data, "input_schema", "inputSchema", "realytics-parser-module-input-v1"
            ),
            output_schema=_field(
                data, "output_schema", "outputSchema", "realytics-parser-module-output-v1"
            ),
            required_locator_precision=_field(
                data, "required_locator_precision", "requiredLocatorPrecision", "none"
            ),
            data_classification=_field(
                data, "data_classification", "dataClassification", "confidential"
            ),
            max_output_bytes=int(
                _field(data, "max_output_bytes", "maxOutputBytes", 5 * 1024 * 1024)
            ),
            active=bool(_field(data, "active", default=True)),
            metadata=_field(data, "metadata", default={}) or {},
        )


@runtime_checkable
class ParsingModule(Protocol):
    """Minimal executable boundary shared by deterministic and AI modules."""

    manifest: Any

    def execute(self, payload: Mapping[str, Any], context: Mapping[str, Any]) -> Any: ...


def _version_key(version: str) -> tuple[tuple[int, object], ...]:
    parts = re.split(r"[.\-+]", str(version))
    return tuple((0, int(part)) if part.isdigit() else (1, part.casefold()) for part in parts)


def split_module_ref(module_ref: str) -> tuple[str, str | None]:
    """Split ``module_id`` or ``module_id@version`` without weakening id rules."""

    raw = str(module_ref or "").strip()
    module_id, marker, version = raw.partition("@")
    _validate_identifier(module_id, label="module reference")
    if marker and not version.strip():
        raise UnknownModuleError(f"Pinned module reference has no version: {raw}")
    return module_id, version.strip() if marker else None


class ParserModuleRegistry:
    """Thread-safe, version-aware registry for independently deployable modules."""

    def __init__(self) -> None:
        self._modules: dict[tuple[str, str], ParsingModule] = {}
        self._manifests: dict[tuple[str, str], ParserModuleManifest] = {}
        self._lock = RLock()

    def register(self, module: ParsingModule) -> ParserModuleManifest:
        execute = getattr(module, "execute", None)
        if not callable(execute):
            raise ParsingRegistryError("Parsing module must expose execute(payload, context)")
        try:
            manifest = ParserModuleManifest.from_value(getattr(module, "manifest"))
        except AttributeError as exc:
            raise InvalidManifestError("Parsing module must expose a manifest") from exc
        key = (manifest.module_id, manifest.version)
        with self._lock:
            if key in self._modules:
                raise ParsingRegistryError(f"Parsing module already registered: {manifest.ref}")
            self._modules[key] = module
            self._manifests[key] = manifest
        return manifest

    def unregister(self, module_id: str, version: str) -> None:
        key = (module_id, version)
        with self._lock:
            self._modules.pop(key, None)
            self._manifests.pop(key, None)

    def get(self, module_id: str, version: str | None = None) -> ParsingModule:
        module_key = _validate_identifier(module_id, label="module_id")
        with self._lock:
            if version is not None:
                try:
                    module = self._modules[(module_key, version)]
                    manifest = self._manifests[(module_key, version)]
                except KeyError as exc:
                    raise UnknownModuleError(f"Unknown parsing module: {module_key}@{version}") from exc
                if not manifest.active:
                    raise UnknownModuleError(
                        f"Parsing module version is inactive or revoked: {module_key}@{version}"
                    )
                return module
            candidates = [
                (manifest, self._modules[key])
                for key, manifest in self._manifests.items()
                if manifest.module_id == module_key and manifest.active
            ]
        if not candidates:
            raise UnknownModuleError(f"No active parsing module registered for: {module_key}")
        return max(candidates, key=lambda item: _version_key(item[0].version))[1]

    def resolve(self, module_ref: str) -> ParsingModule:
        module_id, version = split_module_ref(module_ref)
        return self.get(module_id, version)

    def manifest_for(self, module: ParsingModule) -> ParserModuleManifest:
        """Return the validated manifest held by this registry."""

        raw = ParserModuleManifest.from_value(getattr(module, "manifest", None))
        key = (raw.module_id, raw.version)
        with self._lock:
            registered = self._manifests.get(key)
        if registered is None:
            raise UnknownModuleError(f"Parsing module is not registered: {raw.ref}")
        return registered

    def list_active(self) -> list[ParserModuleManifest]:
        with self._lock:
            manifests = [manifest for manifest in self._manifests.values() if manifest.active]
        return sorted(manifests, key=lambda item: (item.module_id, _version_key(item.version)))

    def find_candidates(
        self,
        *,
        capability: str | None = None,
        document_type: str | None = None,
        asset_type: str | None = None,
        deal_type: str | None = None,
    ) -> list[ParserModuleManifest]:
        """Find active modules compatible with the requested versioned taxonomy type."""

        result: list[ParserModuleManifest] = []
        for manifest in self.list_active():
            declared_capabilities = (
                set(manifest.capabilities)
                .union(manifest.provides)
                .union(manifest.conditional_provides)
            )
            if capability and capability not in declared_capabilities:
                continue
            if not _supports(
                manifest.supported_document_types,
                document_type,
                scope=manifest.document_type_scope,
                taxonomy_version=manifest.document_taxonomy_version,
            ):
                continue
            if not _supports(manifest.supported_asset_types, asset_type):
                continue
            if not _supports(manifest.supported_deal_types, deal_type):
                continue
            result.append(manifest)
        return result

    def snapshot(self) -> list[dict[str, Any]]:
        return [manifest.to_dict() for manifest in self.list_active()]


def _supports(
    supported: tuple[str, ...],
    requested: str | None,
    *,
    scope: str = DOCUMENT_TYPE_SCOPE_LISTED,
    taxonomy_version: str | None = None,
) -> bool:
    if requested is None:
        return True
    if taxonomy_version != DOCUMENT_TAXONOMY_VERSION:
        return False
    requested_key = str(requested).strip().casefold()
    try:
        requested_key = canonical_document_types((requested_key,))[0]
    except (IndexError, ValueError):
        return False
    if scope == DOCUMENT_TYPE_SCOPE_ALL:
        return True
    if scope != DOCUMENT_TYPE_SCOPE_LISTED or not supported:
        return False
    return any(item.casefold() == requested_key for item in supported)


__all__ = [
    "FAILURE_POLICIES",
    "DATA_CLASSIFICATIONS",
    "EXECUTION_CLASSES",
    "LOCATOR_PRECISIONS",
    "DOCUMENT_TYPE_SCOPE_ALL",
    "DOCUMENT_TYPE_SCOPE_LISTED",
    "InvalidManifestError",
    "ParserModuleManifest",
    "ParserModuleRegistry",
    "ParsingModule",
    "ParsingRegistryError",
    "UnknownModuleError",
    "split_module_ref",
]
