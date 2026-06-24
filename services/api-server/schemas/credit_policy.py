from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

CREDIT_POLICY_SCHEMA_VERSION = "realytics-credit-policy-v1"


class CreditPolicyProfile(BaseModel):
    """Lender-specific credit decision thresholds and waiver rules.

    The Credit Review Agent reads these instead of hardcoded constants so the
    same project can be judged under different institutions' risk appetites.
    """

    schemaVersion: str = CREDIT_POLICY_SCHEMA_VERSION
    profileId: str = Field(..., description="Stable profile key, e.g. standard / conservative / aggressive")
    lenderName: str = Field(..., description="Display name of the lending institution or policy")
    hurdleRate: float = Field(default=0.12, description="Minimum acceptable project IRR")
    exitSaleRateReworkThreshold: float = Field(
        default=0.85,
        description="Required Exit sale rate at or above which the deal is sent back for rework (higher = riskier).",
    )
    criticalRiskCountReworkThreshold: int = Field(
        default=3, description="Critical risk count at or above which the deal is sent back for rework."
    )
    marketabilityReworkGrades: List[str] = Field(
        default_factory=lambda: ["D", "E"], description="Marketability grades that force rework."
    )
    maxAdjustedSeniorLtv: Optional[float] = Field(
        default=None,
        description="If set, an enhancement-adjusted senior LTV above this caps the decision at conditional approval.",
    )
    requireCommitteeForConditional: bool = Field(
        default=False, description="Whether conditional approvals must route to a credit committee."
    )
    waiverApproverRole: str = Field(
        default="credit_committee", description="Role required to waive an unresolved material risk under this policy."
    )


_BUILTIN_PROFILES: Dict[str, CreditPolicyProfile] = {
    "standard": CreditPolicyProfile(
        profileId="standard",
        lenderName="표준 여신정책",
        hurdleRate=0.12,
        exitSaleRateReworkThreshold=0.85,
        criticalRiskCountReworkThreshold=3,
        marketabilityReworkGrades=["D", "E"],
        maxAdjustedSeniorLtv=None,
        requireCommitteeForConditional=False,
        waiverApproverRole="credit_committee",
    ),
    "conservative": CreditPolicyProfile(
        profileId="conservative",
        lenderName="보수적 여신정책 (제1금융권형)",
        hurdleRate=0.15,
        exitSaleRateReworkThreshold=0.80,
        criticalRiskCountReworkThreshold=2,
        marketabilityReworkGrades=["C", "D", "E"],
        maxAdjustedSeniorLtv=0.60,
        requireCommitteeForConditional=True,
        waiverApproverRole="credit_committee",
    ),
    "aggressive": CreditPolicyProfile(
        profileId="aggressive",
        lenderName="적극적 여신정책 (캐피탈·증권형)",
        hurdleRate=0.10,
        exitSaleRateReworkThreshold=0.92,
        criticalRiskCountReworkThreshold=4,
        marketabilityReworkGrades=["E"],
        maxAdjustedSeniorLtv=None,
        requireCommitteeForConditional=False,
        waiverApproverRole="credit_officer",
    ),
}

DEFAULT_CREDIT_POLICY_ID = "standard"


def list_credit_policy_profiles() -> List[CreditPolicyProfile]:
    return list(_BUILTIN_PROFILES.values())


def get_credit_policy_profile(profile_id: Optional[str]) -> CreditPolicyProfile:
    if not profile_id:
        return _BUILTIN_PROFILES[DEFAULT_CREDIT_POLICY_ID]
    return _BUILTIN_PROFILES.get(str(profile_id).strip().lower(), _BUILTIN_PROFILES[DEFAULT_CREDIT_POLICY_ID])


def resolve_credit_policy(project_data: Dict[str, Any]) -> CreditPolicyProfile:
    """Resolve the credit policy for a project.

    Order of precedence: an explicit inline profile dict under
    `credit_policy`/`creditPolicy`, then a named profile under
    `credit_policy_profile`/`creditPolicyProfile`, otherwise the standard policy.
    """
    inline = project_data.get("credit_policy") or project_data.get("creditPolicy")
    if isinstance(inline, dict) and inline:
        base = get_credit_policy_profile(inline.get("profileId") or inline.get("profile_id"))
        merged = {**base.model_dump(), **inline}
        merged.setdefault("profileId", base.profileId)
        merged.setdefault("lenderName", base.lenderName)
        return CreditPolicyProfile(**merged)
    name = project_data.get("credit_policy_profile") or project_data.get("creditPolicyProfile")
    return get_credit_policy_profile(name)
