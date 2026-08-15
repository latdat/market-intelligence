"""Shared alert-candidate contract produced by deterministic matching."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


def _reject_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


type NonBlankString = Annotated[str, Field(min_length=1), AfterValidator(_reject_blank)]


class AlertImportance(StrEnum):
    """Current deterministic importance levels for alert candidates."""

    NORMAL = "NORMAL"
    HIGH = "HIGH"


class AlertCandidate(BaseModel):
    """Minimal shared DE -> SWE candidate contract."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    candidate_id: NonBlankString
    user_id: NonBlankString
    article_id: NonBlankString
    matched_at: datetime
    match_reasons: tuple[NonBlankString, ...] = Field(min_length=1)
    importance: AlertImportance
    relevance_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    breaking_eligible: bool

    @model_validator(mode="after")
    def validate_candidate(self) -> Self:
        if self.matched_at.tzinfo is None or self.matched_at.utcoffset() is None:
            raise ValueError("matched_at must include timezone information")
        object.__setattr__(self, "matched_at", self.matched_at.astimezone(UTC))

        if len(self.match_reasons) != len(set(self.match_reasons)):
            raise ValueError("match_reasons must not contain duplicates")

        return self
