"""Validated client attestation for a completed Cash Flow XLSX export."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CASHFLOW_EXPORT_ENGINE_KEYS = (
    "cash_flow_engine",
    "waterfall",
    "tranche_recovery",
    "unsold_ltv",
    "discount_stress",
)
XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


class CashflowExportCompletionRequest(BaseModel):
    """Metadata emitted only after the BFF has generated the XLSX bytes.

    The API revalidates the exact CalculationRun lineage.  This request is an
    authenticated completion attestation, not proof that an artifact was
    stored in the server object archive.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    export_purpose: Literal["draft", "approved"] = Field(default="draft", alias="exportPurpose")

    anchor_calculation_run_id: str = Field(
        alias="anchorCalculationRunId",
        min_length=1,
        max_length=200,
    )
    calculation_run_ids: dict[str, str] = Field(alias="calculationRunIds")
    artifact_sha256: str = Field(
        alias="artifactSha256",
        pattern=r"^[0-9A-Fa-f]{64}$",
    )
    file_name: str = Field(alias="fileName", min_length=1, max_length=255)
    size_bytes: int = Field(alias="sizeBytes", gt=0, le=536_870_912)
    media_type: Literal[
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ] = Field(default=XLSX_MEDIA_TYPE, alias="mediaType")

    @field_validator("anchor_calculation_run_id")
    @classmethod
    def normalize_anchor_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("anchorCalculationRunId must not be blank")
        return normalized

    @field_validator("artifact_sha256")
    @classmethod
    def normalize_sha256(cls, value: str) -> str:
        return value.casefold()

    @field_validator("file_name")
    @classmethod
    def validate_file_name(cls, value: str) -> str:
        normalized = value.strip()
        if (
            not normalized
            or normalized in {".", ".."}
            or PurePosixPath(normalized).name != normalized
            or PureWindowsPath(normalized).name != normalized
            or any(ord(character) < 32 for character in normalized)
            or not normalized.casefold().endswith(".xlsx")
        ):
            raise ValueError("fileName must be a safe XLSX basename")
        return normalized

    @model_validator(mode="after")
    def validate_calculation_run_set(self):
        expected = set(CASHFLOW_EXPORT_ENGINE_KEYS)
        actual = set(self.calculation_run_ids)
        if actual != expected:
            raise ValueError(
                "calculationRunIds must contain the exact Cash Flow export engines"
            )
        normalized_ids: dict[str, str] = {}
        for engine_key in CASHFLOW_EXPORT_ENGINE_KEYS:
            run_id = str(self.calculation_run_ids.get(engine_key) or "").strip()
            if not run_id or len(run_id) > 200:
                raise ValueError(
                    f"calculationRunIds.{engine_key} must be a non-blank run id"
                )
            normalized_ids[engine_key] = run_id
        if normalized_ids["cash_flow_engine"] != self.anchor_calculation_run_id:
            raise ValueError(
                "cash_flow_engine must equal anchorCalculationRunId"
            )
        self.calculation_run_ids = normalized_ids
        return self


__all__ = [
    "CASHFLOW_EXPORT_ENGINE_KEYS",
    "CashflowExportCompletionRequest",
    "XLSX_MEDIA_TYPE",
]
