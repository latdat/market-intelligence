"""Validated models for static source configuration and operational state."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)


class Market(StrEnum):
    """Markets supported in the current product phase."""

    VN = "VN"
    US = "US"
    EU = "EU"
    CN = "CN"


class Domain(StrEnum):
    """Initial intelligence domains."""

    LAW_POLICY = "LAW_POLICY"
    ENERGY = "ENERGY"
    TECHNOLOGY = "TECHNOLOGY"
    REAL_ESTATE = "REAL_ESTATE"
    FINANCE = "FINANCE"


class AcquisitionMethod(StrEnum):
    """Approved source acquisition families."""

    REST_API = "REST_API"
    RSS = "RSS"
    ATOM = "ATOM"
    HTML = "HTML"
    SITEMAP = "SITEMAP"


class SourceType(StrEnum):
    """Source organization/content types documented by the registry guide."""

    GOVERNMENT = "GOVERNMENT"
    REGULATOR = "REGULATOR"
    OFFICIAL_ORGANIZATION = "OFFICIAL_ORGANIZATION"
    NEWS = "NEWS"
    PUBLIC_DATA = "PUBLIC_DATA"


class AuthorityLevel(StrEnum):
    """How authoritative a source is for downstream decisions."""

    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    DISCOVERY = "DISCOVERY"


class HealthStatus(StrEnum):
    """Operational health values, including the existing compatibility value."""

    UNKNOWN = "UNKNOWN"
    ACTIVE = "ACTIVE"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"


class RightsReviewStatus(StrEnum):
    """Explicit status of a source rights review."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class CostType(StrEnum):
    """Cost shapes supported by the current source registry."""

    FREE = "FREE"
    FIXED_MONTHLY = "FIXED_MONTHLY"


type RightsDecision = StrictBool | Literal["REVIEWED"]
type PositiveStrictInt = Annotated[int, Field(strict=True, gt=0)]
type NonNegativeStrictInt = Annotated[int, Field(strict=True, ge=0)]
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


class RegistryModel(BaseModel):
    """Common fail-fast behavior for source registry models."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RateLimitConfig(RegistryModel):
    """Provisional internal representation for a basic request window."""

    max_requests: PositiveStrictInt
    period_seconds: PositiveStrictInt


class AcquisitionConfig(RegistryModel):
    """Static instructions for acquiring one source."""

    method: AcquisitionMethod
    poll_interval_minutes: PositiveStrictInt
    rate_limit: RateLimitConfig | None = None


class RightsConfig(RegistryModel):
    """Explicit source rights decisions without inferred legal policy."""

    can_fetch: StrictBool
    can_store_metadata: StrictBool
    can_store_full_text: RightsDecision
    can_ai_process: RightsDecision
    can_show_snippet: RightsDecision
    can_redistribute_full_text: StrictBool
    rights_review_status: RightsReviewStatus


class CostConfig(RegistryModel):
    """Validated fixed monthly acquisition cost metadata."""

    type: CostType
    monthly_fixed_usd: float = Field(ge=0, allow_inf_nan=False)

    @field_validator("monthly_fixed_usd", mode="before")
    @classmethod
    def reject_non_numeric_cost(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("monthly_fixed_usd must be a number")
        return value

    @model_validator(mode="after")
    def validate_cost_invariant(self) -> Self:
        if self.type is CostType.FREE and self.monthly_fixed_usd != 0:
            raise ValueError("FREE sources must have zero monthly fixed cost")
        if self.type is CostType.FIXED_MONTHLY and self.monthly_fixed_usd <= 0:
            raise ValueError("FIXED_MONTHLY sources must have a positive monthly fixed cost")
        return self


class _SourceMetadata(RegistryModel):
    source_id: SourceId
    name: NonEmptyString
    market: Market
    language: NonEmptyString
    source_type: SourceType
    authority_level: AuthorityLevel
    domains: Annotated[list[Domain], Field(min_length=1)]
    rights: RightsConfig
    cost: CostConfig
    priority: NonNegativeStrictInt

    @field_validator("domains")
    @classmethod
    def reject_duplicate_domains(cls, domains: list[Domain]) -> list[Domain]:
        if len(domains) != len(set(domains)):
            raise ValueError("domains must not contain duplicates")
        return domains


class SourceConfig(_SourceMetadata):
    """Static, human-authored source configuration."""

    acquisition: AcquisitionConfig


class SourceOperationalState(RegistryModel):
    """Dynamic source state maintained by runtime systems."""

    health_status: HealthStatus = HealthStatus.UNKNOWN
    last_success_at: UtcDateTime | None = None
    last_failure_at: UtcDateTime | None = None


class SourceDefinition(_SourceMetadata):
    """Flat compatibility model matching the shared SourceDefinition contract."""

    acquisition_method: AcquisitionMethod
    poll_interval_minutes: PositiveStrictInt
    rate_limit: RateLimitConfig | None = None
    health_status: HealthStatus = HealthStatus.UNKNOWN
    last_success_at: UtcDateTime | None = None
    last_failure_at: UtcDateTime | None = None

    @classmethod
    def from_parts(
        cls,
        config: SourceConfig,
        state: SourceOperationalState | None = None,
    ) -> Self:
        """Build the flat shared boundary without changing either source of truth."""
        operational_state = state or SourceOperationalState()
        return cls(
            source_id=config.source_id,
            name=config.name,
            market=config.market,
            language=config.language,
            source_type=config.source_type,
            authority_level=config.authority_level,
            domains=config.domains,
            acquisition_method=config.acquisition.method,
            poll_interval_minutes=config.acquisition.poll_interval_minutes,
            rate_limit=config.acquisition.rate_limit,
            rights=config.rights,
            cost=config.cost,
            priority=config.priority,
            health_status=operational_state.health_status,
            last_success_at=operational_state.last_success_at,
            last_failure_at=operational_state.last_failure_at,
        )
