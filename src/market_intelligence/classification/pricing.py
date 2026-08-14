"""Effective-dated DeepSeek pricing configuration and deterministic cost estimates."""

import tomllib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from market_intelligence.classification.models import ClassificationUsage

_TOKENS_PER_MILLION = Decimal(1_000_000)


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("pricing timestamp must include timezone information")
    return value.astimezone(UTC)


type UtcDateTime = Annotated[datetime, AfterValidator(_normalize_utc)]


class PricingConfigurationError(ValueError):
    """Raised when versioned pricing cannot be loaded or resolved safely."""


class PricingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PriceWindow(PricingModel):
    """One UTC daily rate window using minute offsets in [0, 1440]."""

    name: str = Field(min_length=1)
    start_minute_utc: int = Field(strict=True, ge=0, le=1439)
    end_minute_utc: int = Field(strict=True, ge=1, le=1440)
    input_cache_hit_usd_per_million: Decimal = Field(ge=0)
    input_cache_miss_usd_per_million: Decimal = Field(ge=0)
    output_usd_per_million: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.start_minute_utc >= self.end_minute_utc:
            raise ValueError("pricing window start must be before its end")
        return self


class PricingSchedule(PricingModel):
    """Rates for one model during a non-overlapping effective interval."""

    pricing_id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    currency: str = Field(pattern=r"^USD$")
    source_url: str = Field(min_length=1)
    verified_at: UtcDateTime
    effective_from: UtcDateTime
    effective_until: UtcDateTime | None = None
    windows: tuple[PriceWindow, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_schedule(self) -> Self:
        if self.effective_until is not None and self.effective_until <= self.effective_from:
            raise ValueError("pricing effective_until must be after effective_from")

        ordered = tuple(sorted(self.windows, key=lambda item: item.start_minute_utc))
        expected_start = 0
        for window in ordered:
            if window.start_minute_utc != expected_start:
                raise ValueError("pricing windows must cover the UTC day without gaps/overlap")
            expected_start = window.end_minute_utc
        if expected_start != 1440:
            raise ValueError("pricing windows must cover all 1440 UTC minutes")
        if len({window.name for window in ordered}) != len(ordered):
            raise ValueError("pricing window names must be unique within a schedule")
        object.__setattr__(self, "windows", ordered)
        return self

    def contains(self, occurred_at: datetime) -> bool:
        return self.effective_from <= occurred_at and (
            self.effective_until is None or occurred_at < self.effective_until
        )

    def window_at(self, occurred_at: datetime) -> PriceWindow:
        minute = occurred_at.hour * 60 + occurred_at.minute
        for window in self.windows:
            if window.start_minute_utc <= minute < window.end_minute_utc:
                return window
        raise AssertionError("validated pricing schedule does not cover the UTC day")


class EstimatedCost(PricingModel):
    pricing_id: str
    pricing_window: str
    amount_usd: Decimal = Field(ge=0)


class PricingCatalog(PricingModel):
    """Validated rate history used without runtime internet access."""

    schedules: tuple[PricingSchedule, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_catalog(self) -> Self:
        grouped: dict[str, list[PricingSchedule]] = {}
        for schedule in self.schedules:
            grouped.setdefault(schedule.model, []).append(schedule)
        for model, schedules in grouped.items():
            ordered = sorted(schedules, key=lambda item: item.effective_from)
            for previous, current in zip(ordered, ordered[1:], strict=False):
                if previous.effective_until is None:
                    raise ValueError(f"open-ended pricing for {model} must be last")
                if previous.effective_until > current.effective_from:
                    raise ValueError(f"pricing schedules overlap for {model}")
        return self

    def estimate(
        self,
        model: str,
        occurred_at: datetime,
        usage: ClassificationUsage,
    ) -> EstimatedCost:
        """Estimate cost from observed usage at one deterministic invocation time."""
        normalized_time = _normalize_utc(occurred_at)
        matches = [
            schedule
            for schedule in self.schedules
            if schedule.model == model and schedule.contains(normalized_time)
        ]
        if len(matches) != 1:
            raise PricingConfigurationError(
                f"expected one pricing schedule for model {model} at {normalized_time.isoformat()}"
            )
        schedule = matches[0]
        window = schedule.window_at(normalized_time)
        amount = (
            Decimal(usage.prompt_cache_hit_tokens) * window.input_cache_hit_usd_per_million
            + Decimal(usage.prompt_cache_miss_tokens) * window.input_cache_miss_usd_per_million
            + Decimal(usage.completion_tokens) * window.output_usd_per_million
        ) / _TOKENS_PER_MILLION
        return EstimatedCost(
            pricing_id=schedule.pricing_id,
            pricing_window=window.name,
            amount_usd=amount,
        )


def load_pricing_catalog(path: Path) -> PricingCatalog:
    """Load a versioned TOML catalog with path context and no network access."""
    try:
        with path.open("rb") as config_file:
            payload = tomllib.load(config_file)
        return PricingCatalog.model_validate(payload)
    except (OSError, tomllib.TOMLDecodeError, ValueError) as error:
        raise PricingConfigurationError(f"invalid pricing configuration: {path}") from error
