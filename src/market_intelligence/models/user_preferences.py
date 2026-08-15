"""Shared user preference contract consumed by DE matching."""

from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from market_intelligence.classification.models import Topic
from market_intelligence.source_registry import Domain, Market


def _reject_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


type NonBlankString = Annotated[str, Field(min_length=1), AfterValidator(_reject_blank)]


class UserPreference(BaseModel):
    """Minimal shared Product/SWE -> DE preference contract."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    user_id: NonBlankString
    markets: tuple[Market, ...]
    categories: tuple[Domain, ...]
    topics: tuple[Topic, ...]
    muted_source_ids: tuple[NonBlankString, ...]
    muted_topics: tuple[Topic, ...]
    breaking_alert_enabled: bool
    hourly_update_enabled: bool
    daily_digest_enabled: bool

    @model_validator(mode="after")
    def validate_collections(self) -> Self:
        for name, values in (
            ("markets", self.markets),
            ("categories", self.categories),
            ("topics", self.topics),
            ("muted_source_ids", self.muted_source_ids),
            ("muted_topics", self.muted_topics),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")
        return self
