"""Typed contracts for invite-first tenant access lifecycle."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TenantRole(StrEnum):
    ADMIN = "admin"
    REVIEWER = "reviewer"
    ANALYST = "analyst"
    VIEWER = "viewer"
    LEGAL_REVIEWER = "legal_reviewer"
    LEGAL_COUNSEL_APPROVER = "legal_counsel_approver"
    EXTERNAL_COUNSEL = "external_counsel"
    COMPLIANCE_OFFICER = "compliance_officer"


class IssueTenantInvitationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: TenantRole
    invitedEmail: str | None = Field(default=None, max_length=320)
    invitedIssuer: str | None = Field(default=None, max_length=1000)
    invitedSubject: str | None = Field(default=None, max_length=1000)
    expiresInHours: int = Field(default=72, ge=1, le=720)

    @model_validator(mode="after")
    def normalize_binding(self) -> "IssueTenantInvitationInput":
        self.invitedEmail = _optional_text(self.invitedEmail)
        self.invitedIssuer = _optional_text(self.invitedIssuer)
        self.invitedSubject = _optional_text(self.invitedSubject)
        if bool(self.invitedIssuer) != bool(self.invitedSubject):
            raise ValueError(
                "invitedIssuer and invitedSubject must be supplied together"
            )
        if self.invitedEmail and "@" not in self.invitedEmail:
            raise ValueError("invitedEmail must be a valid email address")
        if self.invitedEmail:
            self.invitedEmail = self.invitedEmail.casefold()
        if self.invitedIssuer:
            self.invitedIssuer = self.invitedIssuer.rstrip("/")
        return self


class AcceptTenantInvitationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=32, max_length=512)


class RevokeTenantInvitationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=1000)


class ChangeTenantMembershipRoleInput(BaseModel):
    """Provider-neutral, optimistic role-change command.

    ``targetIssuer`` and ``targetSubject`` identify the verified principal.
    Email, provider profile metadata, and client-supplied tenant identifiers are
    deliberately not part of the mutation contract.
    """

    model_config = ConfigDict(extra="forbid")

    targetIssuer: str = Field(min_length=1, max_length=1000)
    targetSubject: str = Field(min_length=1, max_length=1000)
    expectedMembershipVersion: int = Field(ge=1)
    role: TenantRole
    reason: str = Field(min_length=3, max_length=1000)

    @model_validator(mode="after")
    def normalize_target(self) -> "ChangeTenantMembershipRoleInput":
        self.targetIssuer = self.targetIssuer.strip().rstrip("/")
        self.targetSubject = self.targetSubject.strip()
        self.reason = self.reason.strip()
        if not self.targetIssuer or not self.targetSubject:
            raise ValueError("targetIssuer and targetSubject must be non-blank")
        if len(self.reason) < 3:
            raise ValueError("reason must contain at least 3 non-space characters")
        return self


class TenantInvitationView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invitationId: str
    tenantId: str
    role: TenantRole
    status: str
    invitedEmail: str | None = None
    invitedIssuer: str | None = None
    invitedSubject: str | None = None
    issuedByIssuer: str
    issuedBySubject: str
    issuedAt: datetime
    expiresAt: datetime
    acceptedByIssuer: str | None = None
    acceptedBySubject: str | None = None
    acceptedAt: datetime | None = None
    revokedAt: datetime | None = None
    revocationReason: str | None = None


class TenantMembershipView(BaseModel):
    """Minimal authorized tenant-directory projection.

    This intentionally excludes OAuth/provider payloads, token claims,
    verification methods, legacy user IDs, and membership source internals.
    """

    model_config = ConfigDict(extra="forbid")

    tenantId: str
    issuer: str
    subject: str
    principalType: str
    principalStatus: str
    email: str | None = None
    displayName: str | None = None
    role: TenantRole
    status: str
    membershipVersion: int = Field(ge=1)
    updatedAt: datetime


def _optional_text(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


__all__ = [
    "AcceptTenantInvitationInput",
    "ChangeTenantMembershipRoleInput",
    "IssueTenantInvitationInput",
    "RevokeTenantInvitationInput",
    "TenantInvitationView",
    "TenantMembershipView",
    "TenantRole",
]
