"""Validated shared and internal contracts for article classification."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from market_intelligence.source_registry import AuthorityLevel, Domain, Market, SourceType

CLASSIFIER_VERSION = "classification-v1"
HYBRID_CLASSIFIER_VERSION = "classification-v2"
DETERMINISTIC_RULES_VERSION = "deterministic-rules-v1"
PROMPT_VERSION = "classification-prompt-v1"
TAXONOMY_VERSION = "classification-taxonomy-v1"

type NonEmptyString = Annotated[str, Field(min_length=1)]
type ClassifierVersion = Annotated[
    str,
    Field(pattern=r"^classification-v[1-9][0-9]*$", max_length=64),
]


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include timezone information")
    return value.astimezone(UTC)


type UtcDateTime = Annotated[datetime, AfterValidator(_normalize_utc)]


class Topic(StrEnum):
    """Controlled topic vocabulary for classification-taxonomy-v1."""

    AI = "AI"
    BANKING = "BANKING"
    INTEREST_RATES = "INTEREST_RATES"
    OIL_GAS = "OIL_GAS"
    REAL_ESTATE = "REAL_ESTATE"
    REGULATION = "REGULATION"
    RENEWABLE_ENERGY = "RENEWABLE_ENERGY"
    SEMICONDUCTORS = "SEMICONDUCTORS"


class ClassificationMethod(StrEnum):
    """DE-internal method that produced one successful classification."""

    DETERMINISTIC = "DETERMINISTIC"
    DEEPSEEK = "DEEPSEEK"


_MARKET_ORDER = {
    Market.VN: 0,
    Market.US: 1,
    Market.EU: 2,
    Market.CN: 3,
}


class ClassificationModel(BaseModel):
    """Strict fail-fast behavior for classification boundaries."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ClassificationInput(ClassificationModel):
    """Minimal semantic metadata that may be serialized into the provider prompt."""

    title: NonEmptyString
    description: str | None
    source_market: Market
    source_language: NonEmptyString
    source_domains: tuple[Domain, ...] = Field(min_length=1)
    source_type: SourceType
    authority_level: AuthorityLevel

    @model_validator(mode="after")
    def validate_and_normalize_domains(self) -> "ClassificationInput":
        if len(self.source_domains) != len(set(self.source_domains)):
            raise ValueError("source_domains must not contain duplicates")
        canonical = tuple(sorted(self.source_domains, key=lambda value: value.value))
        object.__setattr__(self, "source_domains", canonical)
        return self


class _SemanticClassification(ClassificationModel):
    """Provider-controlled semantic fields with canonical list ordering."""

    is_relevant: bool
    markets: tuple[Market, ...] = Field(max_length=4)
    category: Domain | None
    topics: tuple[Topic, ...] = Field(max_length=5)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_semantics(self) -> "_SemanticClassification":
        if len(self.markets) != len(set(self.markets)):
            raise ValueError("markets must not contain duplicates")
        if len(self.topics) != len(set(self.topics)):
            raise ValueError("topics must not contain duplicates")

        if self.is_relevant:
            if not self.markets:
                raise ValueError("relevant classifications require at least one market")
            if self.category is None:
                raise ValueError("relevant classifications require a primary category")
        elif self.markets or self.category is not None or self.topics:
            raise ValueError(
                "irrelevant classifications require empty markets/topics and null category"
            )

        canonical_markets = tuple(sorted(self.markets, key=_MARKET_ORDER.__getitem__))
        canonical_topics = tuple(sorted(self.topics, key=lambda value: value.value))
        object.__setattr__(self, "markets", canonical_markets)
        object.__setattr__(self, "topics", canonical_topics)
        return self


class ProviderClassificationOutput(_SemanticClassification):
    """Only semantic fields that the model is permitted to control."""


class ClassifiedArticle(_SemanticClassification):
    """Minimal shared DE/SWE classification contract."""

    article_id: NonEmptyString
    classifier_version: ClassifierVersion
    classified_at: UtcDateTime


class ClassificationUsage(ClassificationModel):
    """Observed provider usage, aggregated across measurable attempts."""

    prompt_tokens: int = Field(ge=0)
    prompt_cache_hit_tokens: int = Field(ge=0)
    prompt_cache_miss_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_token_totals(self) -> "ClassificationUsage":
        if self.prompt_tokens != (self.prompt_cache_hit_tokens + self.prompt_cache_miss_tokens):
            raise ValueError("prompt_tokens must equal cache-hit plus cache-miss prompt tokens")
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("total_tokens must equal prompt plus completion tokens")
        return self

    @classmethod
    def zero(cls) -> "ClassificationUsage":
        """Return a validated zero-usage accumulator."""
        return cls(
            prompt_tokens=0,
            prompt_cache_hit_tokens=0,
            prompt_cache_miss_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )

    def __add__(self, other: "ClassificationUsage") -> "ClassificationUsage":
        return ClassificationUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            prompt_cache_hit_tokens=(self.prompt_cache_hit_tokens + other.prompt_cache_hit_tokens),
            prompt_cache_miss_tokens=(
                self.prompt_cache_miss_tokens + other.prompt_cache_miss_tokens
            ),
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


class ClassificationResult(ClassificationModel):
    """Shared result plus provider/operational metadata owned by DE."""

    classified_article: ClassifiedArticle
    classification_method: ClassificationMethod
    requested_model: NonEmptyString
    provider_model: str | None
    prompt_version: NonEmptyString
    taxonomy_version: NonEmptyString
    usage: ClassificationUsage
    estimated_cost_usd: Decimal = Field(ge=0)
    pricing_id: str | None
    pricing_window: str | None
    duration_ms: int = Field(ge=0)
    provider_attempts: int = Field(ge=0, le=3)
    provider_request_id: str | None
    system_fingerprint: str | None

    @model_validator(mode="after")
    def validate_method_metadata(self) -> "ClassificationResult":
        provider_lineage = (
            self.provider_model,
            self.provider_request_id,
            self.system_fingerprint,
        )
        pricing = (self.pricing_id, self.pricing_window)
        if self.classification_method is ClassificationMethod.DETERMINISTIC:
            if self.provider_attempts != 0:
                raise ValueError("deterministic success requires zero provider attempts")
            if self.usage != ClassificationUsage.zero() or self.estimated_cost_usd != 0:
                raise ValueError("deterministic success requires zero usage and cost")
            if any(value is not None for value in (*provider_lineage, *pricing)):
                raise ValueError("deterministic success cannot contain provider metadata")
        else:
            if not 1 <= self.provider_attempts <= 3:
                raise ValueError("DeepSeek success requires one to three provider attempts")
            if any(value is None for value in pricing):
                raise ValueError("DeepSeek success requires pricing metadata")
        return self
