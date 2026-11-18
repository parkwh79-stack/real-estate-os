"""Self-service display-name input; identity and permissions are server-owned."""
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UpdateAccountProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    displayName: str = Field(min_length=1, max_length=200, strict=True)

    @field_validator("displayName")
    @classmethod
    def readable_name(cls, value: str) -> str:
        if re.search(r"[\x00-\x1f\x7f]", value) or re.fullmatch(
            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value, re.IGNORECASE
        ):
            raise ValueError("Enter a display name instead of an account identifier")
        return value
