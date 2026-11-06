"""Typed, provider-neutral contracts for verified-principal onboarding."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OnboardingRequestType(StrEnum):
    EXISTING_ORGANIZATION = "existing_organization"
    NEW_ORGANIZATION = "new_organization"


class OnboardingRequestedRole(StrEnum):
    ANALYST = "analyst"
    VIEWER = "viewer"


class OnboardingAssignedRole(StrEnum):
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class OnboardingDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class SaveOnboardingProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    displayName: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=50)
    jobTitle: str | None = Field(default=None, max_length=200)
    termsAccepted: Literal[True]
    privacyAccepted: Literal[True]
    termsVersion: str = Field(min_length=1, max_length=100)
    privacyVersion: str = Field(min_length=1, max_length=100)
    expectedProfileVersion: int = Field(ge=0)

    @model_validator(mode="after")
    def normalize_profile(self) -> "SaveOnboardingProfileInput":
        self.displayName = _required_text(self.displayName, "displayName")
        self.phone = _optional_text(self.phone)
        self.jobTitle = _optional_text(self.jobTitle)
        self.termsVersion = _required_text(
            self.termsVersion, "termsVersion"
        )
        self.privacyVersion = _required_text(
            self.privacyVersion, "privacyVersion"
        )
        return self


class CreateTenantAccessRequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requestType: OnboardingRequestType
    organizationId: str | None = Field(default=None, max_length=200)
    organizationName: str | None = Field(default=None, max_length=300)
    businessRegistrationNumber: str | None = Field(
        default=None,
        max_length=64,
    )
    requestedRole: OnboardingRequestedRole = OnboardingRequestedRole.ANALYST
    message: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def normalize_request(self) -> "CreateTenantAccessRequestInput":
        self.organizationId = _optional_text(self.organizationId)
        self.organizationName = _optional_text(self.organizationName)
        self.businessRegistrationNumber = _optional_text(
            self.businessRegistrationNumber
        )
        self.message = _optional_text(self.message)
        if self.requestType == OnboardingRequestType.EXISTING_ORGANIZATION:
            if not self.organizationId or self.organizationName:
                raise ValueError(
                    "existing_organization requires organizationId only"
                )
            if self.businessRegistrationNumber:
                raise ValueError(
                    "businessRegistrationNumber is only for new_organization"
                )
        elif not self.organizationName or self.organizationId:
            raise ValueError(
                "new_organization requires organizationName and no organizationId"
            )
        else:
            self.businessRegistrationNumber = _business_registration_number(
                self.businessRegistrationNumber
            )
        return self


class ResubmitTenantAccessRequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRequestVersion: int = Field(ge=1)
    requestedRole: OnboardingRequestedRole | None = None
    message: str | None = Field(default=None, max_length=2000)
    organizationName: str | None = Field(default=None, max_length=300)
    businessRegistrationNumber: str | None = Field(
        default=None,
        max_length=64,
    )

    @model_validator(mode="after")
    def normalize_resubmission(self) -> "ResubmitTenantAccessRequestInput":
        self.message = _optional_text(self.message)
        self.organizationName = _optional_text(self.organizationName)
        self.businessRegistrationNumber = _optional_text(
            self.businessRegistrationNumber
        )
        if self.businessRegistrationNumber is not None:
            self.businessRegistrationNumber = _business_registration_number(
                self.businessRegistrationNumber
            )
        return self


class DecideTenantAccessRequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: OnboardingDecision
    expectedRequestVersion: int = Field(ge=1)
    assignedRole: OnboardingAssignedRole | None = None
    reason: str = Field(min_length=3, max_length=2000)

    @model_validator(mode="after")
    def normalize_decision(self) -> "DecideTenantAccessRequestInput":
        self.reason = _required_text(self.reason, "reason")
        if len(self.reason) < 3:
            raise ValueError("reason must contain at least 3 characters")
        if (
            self.decision == OnboardingDecision.REJECT
            and self.assignedRole is not None
        ):
            raise ValueError("assignedRole is only valid for approval")
        return self


class OnboardingProfileView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issuer: str
    subject: str
    contactEmail: str | None = None
    displayName: str
    phone: str | None = None
    jobTitle: str | None = None
    termsVersion: str
    privacyVersion: str
    termsAcceptedAt: datetime
    privacyAcceptedAt: datetime
    profileVersion: int = Field(ge=1)
    updatedAt: datetime


class OnboardingOrganizationView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organizationId: str
    name: str


class TenantAccessRequestView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requestId: str
    requestType: OnboardingRequestType
    requesterIssuer: str
    requesterSubject: str
    requesterDisplayName: str | None = None
    requesterEmail: str | None = None
    profileVersionSnapshot: int = Field(ge=1)
    termsVersionSnapshot: str
    privacyVersionSnapshot: str
    profileSnapshotHash: str = Field(pattern=r"^[0-9a-f]{64}$")
    organizationId: str | None = None
    organizationName: str | None = None
    businessRegistrationNumber: str | None = None
    requestedRole: OnboardingRequestedRole
    message: str | None = None
    status: str
    requestVersion: int = Field(ge=1)
    submittedAt: datetime
    decisionAt: datetime | None = None
    decisionReason: str | None = None
    approvedOrganizationId: str | None = None
    createdAt: datetime
    updatedAt: datetime


def _optional_text(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _required_text(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} must be non-blank")
    return normalized


def _business_registration_number(value: str | None) -> str:
    compact = re.sub(r"[\s-]", "", str(value or "").strip())
    if len(compact) != 10 or not compact.isascii() or not compact.isdigit():
        raise ValueError(
            "businessRegistrationNumber must contain exactly 10 digits"
        )
    return compact


__all__ = [
    "CreateTenantAccessRequestInput",
    "DecideTenantAccessRequestInput",
    "OnboardingDecision",
    "OnboardingAssignedRole",
    "OnboardingOrganizationView",
    "OnboardingProfileView",
    "OnboardingRequestedRole",
    "OnboardingRequestType",
    "ResubmitTenantAccessRequestInput",
    "SaveOnboardingProfileInput",
    "TenantAccessRequestView",
]
