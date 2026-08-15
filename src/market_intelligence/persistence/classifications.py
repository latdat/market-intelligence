"""Durable classification lifecycle contracts owned by the data layer."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Protocol, Self
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from market_intelligence.classification import (
    ClassificationError,
    ClassificationResult,
    ClassificationUsage,
    ClassifiedArticle,
    Topic,
)
from market_intelligence.source_registry import Domain, Market

type NonEmptyString = Annotated[str, Field(min_length=1)]


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include timezone information")
    return value.astimezone(UTC)


type UtcDateTime = Annotated[datetime, AfterValidator(_normalize_utc)]

_PERSISTED_MARKET_ORDER = {
    Market.VN: 0,
    Market.US: 1,
    Market.EU: 2,
    Market.CN: 3,
}


class ClassificationPersistenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ClassificationStatus(StrEnum):
    PROCESSING = "PROCESSING"
    RETRYABLE = "RETRYABLE"
    SUCCEEDED = "SUCCEEDED"
    QUARANTINED = "QUARANTINED"


class EnqueueOutcome(StrEnum):
    CREATED = "CREATED"
    EXISTING_RETRYABLE = "EXISTING_RETRYABLE"
    EXISTING_PROCESSING = "EXISTING_PROCESSING"
    ALREADY_SUCCEEDED = "ALREADY_SUCCEEDED"
    QUARANTINED = "QUARANTINED"
    LINEAGE_MISMATCH = "LINEAGE_MISMATCH"


class CompletionOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    ALREADY_SUCCEEDED = "ALREADY_SUCCEEDED"
    LOST_CLAIM = "LOST_CLAIM"


class LeaseRenewalOutcome(StrEnum):
    RENEWED = "RENEWED"
    LOST_CLAIM = "LOST_CLAIM"


class FailureOutcome(StrEnum):
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    QUARANTINED = "QUARANTINED"
    LOST_CLAIM = "LOST_CLAIM"


class FailureDisposition(StrEnum):
    RETRY_15_MINUTES = "RETRY_15_MINUTES"
    RETRY_60_MINUTES = "RETRY_60_MINUTES"
    QUARANTINE = "QUARANTINE"


class ClassificationKey(ClassificationPersistenceModel):
    article_id: NonEmptyString
    classifier_version: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^classification-v[1-9][0-9]*$",
    )


class ClassificationLineage(ClassificationPersistenceModel):
    requested_model: NonEmptyString = Field(max_length=128)
    prompt_version: NonEmptyString = Field(max_length=128)
    taxonomy_version: NonEmptyString = Field(max_length=128)


class ClassificationRecord(ClassificationPersistenceModel):
    """Validated internal representation of one durable classification row."""

    article_id: NonEmptyString
    classifier_version: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^classification-v[1-9][0-9]*$",
    )
    status: ClassificationStatus

    is_relevant: bool | None
    markets: tuple[Market, ...] | None = Field(max_length=4)
    category: Domain | None
    topics: tuple[Topic, ...] | None = Field(max_length=5)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    classified_at: UtcDateTime | None

    requested_model: NonEmptyString
    provider_model: str | None
    prompt_version: NonEmptyString
    taxonomy_version: NonEmptyString
    provider_request_id: str | None
    system_fingerprint: str | None

    prompt_tokens: int = Field(ge=0)
    prompt_cache_hit_tokens: int = Field(ge=0)
    prompt_cache_miss_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost_usd: Decimal = Field(ge=0)
    last_pricing_id: str | None
    last_pricing_window: str | None

    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1, le=12)
    last_provider_attempts: int | None = Field(default=None, ge=0, le=3)

    claim_token: UUID | None
    claimed_at: UtcDateTime | None
    lease_expires_at: UtcDateTime | None
    next_attempt_at: UtcDateTime | None

    last_error_category: str | None
    last_http_status: int | None = Field(default=None, ge=100, le=599)
    last_error_retryable: bool | None
    last_error_at: UtcDateTime | None
    quarantined_at: UtcDateTime | None
    created_at: UtcDateTime
    updated_at: UtcDateTime

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        if self.attempt_count > self.max_attempts:
            raise ValueError("attempt_count must not exceed max_attempts")
        if self.prompt_tokens != (self.prompt_cache_hit_tokens + self.prompt_cache_miss_tokens):
            raise ValueError("prompt token cache totals are inconsistent")
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("total token count is inconsistent")

        semantic_values = (
            self.is_relevant,
            self.markets,
            self.category,
            self.topics,
            self.confidence,
            self.classified_at,
        )
        if self.status is ClassificationStatus.SUCCEEDED:
            if any(
                value is None
                for value in (
                    self.is_relevant,
                    self.markets,
                    self.topics,
                    self.confidence,
                    self.classified_at,
                )
            ):
                raise ValueError("SUCCEEDED requires a complete semantic result")
            assert self.is_relevant is not None
            assert self.markets is not None
            assert self.topics is not None
            if len(self.markets) != len(set(self.markets)):
                raise ValueError("persisted markets must not contain duplicates")
            if self.markets != tuple(sorted(self.markets, key=_PERSISTED_MARKET_ORDER.__getitem__)):
                raise ValueError("persisted markets must use canonical order")
            if len(self.topics) != len(set(self.topics)):
                raise ValueError("persisted topics must not contain duplicates")
            if self.topics != tuple(sorted(self.topics, key=lambda value: value.value)):
                raise ValueError("persisted topics must use canonical order")
            if self.is_relevant:
                if not self.markets or self.category is None:
                    raise ValueError("relevant success requires markets and category")
            elif self.markets or self.category is not None or self.topics:
                raise ValueError("irrelevant success requires empty semantic values")
            if self.claim_token is not None or self.lease_expires_at is not None:
                raise ValueError("SUCCEEDED cannot retain a claim")
            if any(
                value is not None
                for value in (
                    self.last_error_category,
                    self.last_http_status,
                    self.last_error_retryable,
                    self.last_error_at,
                )
            ):
                raise ValueError("SUCCEEDED cannot retain terminal error metadata")
        elif any(value is not None for value in semantic_values):
            raise ValueError("non-success records cannot contain semantic result fields")

        provider_lineage = (self.provider_model, self.provider_request_id, self.system_fingerprint)
        if self.status is not ClassificationStatus.SUCCEEDED and any(
            value is not None for value in provider_lineage
        ):
            raise ValueError("non-success records cannot contain provider result lineage")

        if self.status is ClassificationStatus.PROCESSING:
            if self.claim_token is None or self.claimed_at is None or self.lease_expires_at is None:
                raise ValueError("PROCESSING requires claim token and lease timestamps")
            if self.lease_expires_at <= self.claimed_at:
                raise ValueError("lease must expire after claim time")
            if self.next_attempt_at is not None:
                raise ValueError("PROCESSING cannot have next_attempt_at")
        elif (
            self.claim_token is not None
            or self.claimed_at is not None
            or self.lease_expires_at is not None
        ):
            raise ValueError("non-processing records cannot retain claim fields")

        if self.status is ClassificationStatus.RETRYABLE:
            if self.next_attempt_at is None:
                raise ValueError("RETRYABLE requires next_attempt_at")
            if self.attempt_count >= self.max_attempts:
                raise ValueError("RETRYABLE requires remaining invocation budget")
        elif self.next_attempt_at is not None:
            raise ValueError("only RETRYABLE may have next_attempt_at")

        if self.status is ClassificationStatus.QUARANTINED:
            if self.quarantined_at is None or not self.last_error_category:
                raise ValueError("QUARANTINED requires timestamp and error category")
        elif self.quarantined_at is not None:
            raise ValueError("only QUARANTINED may have quarantined_at")
        return self

    @property
    def key(self) -> ClassificationKey:
        return ClassificationKey(
            article_id=self.article_id,
            classifier_version=self.classifier_version,
        )

    @property
    def lineage(self) -> ClassificationLineage:
        return ClassificationLineage(
            requested_model=self.requested_model,
            prompt_version=self.prompt_version,
            taxonomy_version=self.taxonomy_version,
        )

    def to_classified_article(self) -> ClassifiedArticle:
        if self.status is not ClassificationStatus.SUCCEEDED:
            raise ValueError("only SUCCEEDED records have a ClassifiedArticle")
        assert self.is_relevant is not None
        assert self.markets is not None
        assert self.topics is not None
        assert self.confidence is not None
        assert self.classified_at is not None
        return ClassifiedArticle(
            article_id=self.article_id,
            classifier_version=self.classifier_version,
            is_relevant=self.is_relevant,
            markets=self.markets,
            category=self.category,
            topics=self.topics,
            confidence=self.confidence,
            classified_at=self.classified_at,
        )


class ClassificationClaim(ClassificationPersistenceModel):
    key: ClassificationKey
    lineage: ClassificationLineage
    claim_token: UUID
    claimed_at: UtcDateTime
    lease_expires_at: UtcDateTime
    attempt_count: int = Field(ge=1)
    max_attempts: int = Field(ge=1, le=12)

    @model_validator(mode="after")
    def validate_attempt_budget(self) -> Self:
        if self.attempt_count > self.max_attempts:
            raise ValueError("claim attempt_count must not exceed max_attempts")
        return self

    @classmethod
    def from_record(cls, record: ClassificationRecord) -> "ClassificationClaim":
        if record.status is not ClassificationStatus.PROCESSING:
            raise ValueError("classification claim requires a PROCESSING record")
        assert record.claim_token is not None
        assert record.claimed_at is not None
        assert record.lease_expires_at is not None
        return cls(
            key=record.key,
            lineage=record.lineage,
            claim_token=record.claim_token,
            claimed_at=record.claimed_at,
            lease_expires_at=record.lease_expires_at,
            attempt_count=record.attempt_count,
            max_attempts=record.max_attempts,
        )


class ClassificationFailure(ClassificationPersistenceModel):
    error_category: NonEmptyString
    retryable: bool
    provider_attempts: int = Field(ge=0, le=3)
    last_http_status: int | None = Field(default=None, ge=100, le=599)
    usage: ClassificationUsage
    estimated_cost_usd: Decimal = Field(ge=0)
    pricing_id: str | None
    pricing_window: str | None

    @classmethod
    def from_error(cls, error: ClassificationError) -> "ClassificationFailure":
        return cls(
            error_category=error.category.value,
            retryable=error.retryable,
            provider_attempts=error.provider_attempts,
            last_http_status=error.last_http_status,
            usage=error.usage or ClassificationUsage.zero(),
            estimated_cost_usd=error.estimated_cost_usd or Decimal(0),
            pricing_id=error.pricing_id,
            pricing_window=None,
        )


class EnqueueResult(ClassificationPersistenceModel):
    outcome: EnqueueOutcome
    record: ClassificationRecord


class CompletionResult(ClassificationPersistenceModel):
    outcome: CompletionOutcome
    record: ClassificationRecord | None


class LeaseRenewalResult(ClassificationPersistenceModel):
    outcome: LeaseRenewalOutcome
    record: ClassificationRecord | None


class FailureResult(ClassificationPersistenceModel):
    outcome: FailureOutcome
    record: ClassificationRecord | None


class ClassificationPersistenceError(RuntimeError):
    """Sanitized persistence failure that never includes article content or secrets."""

    def __init__(self, key: ClassificationKey, operation: str) -> None:
        self.key = key
        self.operation = operation
        super().__init__(
            f"classification persistence {operation} failed for "
            f"({key.article_id}, {key.classifier_version})"
        )


class ClassificationLineageMismatchError(ClassificationPersistenceError):
    def __init__(self, key: ClassificationKey, mismatched_fields: tuple[str, ...]) -> None:
        self.mismatched_fields = mismatched_fields
        super().__init__(key, "LINEAGE_MISMATCH")


class ClassificationRepository(Protocol):
    def enqueue(
        self,
        key: ClassificationKey,
        lineage: ClassificationLineage,
        *,
        max_attempts: int = 3,
    ) -> EnqueueResult: ...

    def get(self, key: ClassificationKey) -> ClassificationRecord | None: ...

    def get_succeeded(self, key: ClassificationKey) -> ClassifiedArticle | None: ...

    def claim_next(
        self,
        classifier_version: str,
        claim_token: UUID,
        *,
        lease_seconds: int = 300,
    ) -> ClassificationClaim | None: ...

    def renew_lease(
        self,
        claim: ClassificationClaim,
        *,
        lease_seconds: int = 300,
    ) -> LeaseRenewalResult: ...

    def complete_success(
        self,
        claim: ClassificationClaim,
        result: ClassificationResult,
    ) -> CompletionResult: ...

    def record_failure(
        self,
        claim: ClassificationClaim,
        failure: ClassificationFailure,
        *,
        disposition: FailureDisposition,
    ) -> FailureResult: ...
