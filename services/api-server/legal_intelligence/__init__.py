"""Evidence-first legal intelligence contracts.

This package deliberately does not import the project Legal Pod.  It is the
provider/search core that the Pod can orchestrate without making MCP a source
of truth.
"""

from .corpus_identity import (
    OfficialResourceIdentity,
    legal_resource_crosswalk_id,
)
from .effective_time import (
    EffectiveLineageDecision,
    EffectiveTimeConflict,
    EffectiveVersionCandidate,
    EffectiveVersionWindow,
    validate_effective_time_lineage,
)
from .models import (
    CitationAttestation,
    CoverageStatus,
    DataClassification,
    FreshnessStatus,
    LegalResourceKind,
    LegalSearchQuery,
    LegalSearchResponse,
    ProviderResult,
    ProviderStatus,
    SearchHit,
    SearchStage,
    SourceAuthority,
    SourceTransport,
    TrustLevel,
    build_attestation,
    canonical_content_hash,
)
from .orchestrator import LegalIntelligenceOrchestrator
from .providers import (
    LAW_GO_KR_TARGETS,
    LawGoKrDirectProvider,
    McpBridgeProvider,
    ProviderProtocol,
)
from .search import InMemoryVerifiedSnapshotProvider, LegalSnapshot

__all__ = [
    "CitationAttestation",
    "CoverageStatus",
    "DataClassification",
    "FreshnessStatus",
    "EffectiveLineageDecision",
    "EffectiveTimeConflict",
    "EffectiveVersionCandidate",
    "EffectiveVersionWindow",
    "InMemoryVerifiedSnapshotProvider",
    "LAW_GO_KR_TARGETS",
    "LawGoKrDirectProvider",
    "LegalIntelligenceOrchestrator",
    "LegalResourceKind",
    "LegalSearchQuery",
    "LegalSearchResponse",
    "LegalSnapshot",
    "McpBridgeProvider",
    "ProviderProtocol",
    "ProviderResult",
    "ProviderStatus",
    "OfficialResourceIdentity",
    "SearchHit",
    "SearchStage",
    "SourceAuthority",
    "SourceTransport",
    "TrustLevel",
    "build_attestation",
    "canonical_content_hash",
    "legal_resource_crosswalk_id",
    "validate_effective_time_lineage",
]
