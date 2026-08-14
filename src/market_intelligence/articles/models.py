"""Validated article boundary models for ingestion and normalization stages."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from market_intelligence.source_registry import Market

type NonEmptyString = Annotated[str, Field(min_length=1)]
type SourceId = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$"),
]


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include timezone information")
    return value.astimezone(UTC)


type UtcDateTime = Annotated[datetime, AfterValidator(_normalize_utc)]


class ArticleModel(BaseModel):
    """Common fail-fast behavior for article boundary models."""

    model_config = ConfigDict(extra="forbid")


class RawArticle(ArticleModel):
    """Connector output that preserves upstream values before normalization."""

    source_id: SourceId
    source_item_id: str | None = None
    url: NonEmptyString
    title: str | None = None
    description: str | None = None
    published_at_raw: str | None = None
    language_hint: str | None = None
    retrieved_at: UtcDateTime
    raw_metadata: dict[str, object] = Field(default_factory=dict)


class CanonicalArticle(ArticleModel):
    """Normalized article contract for deterministic downstream processing."""

    article_id: NonEmptyString
    source_id: SourceId
    source_item_id: str | None = None
    url: NonEmptyString
    canonical_url: NonEmptyString
    title: NonEmptyString
    description: str | None = None
    language: NonEmptyString
    market: Market
    published_at: UtcDateTime | None = None
    discovered_at: UtcDateTime
    content_hash: NonEmptyString
