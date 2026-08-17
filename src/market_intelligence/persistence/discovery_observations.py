"""Bounded discovery-only persistence contracts owned by the data layer.

This boundary stores aggregates, never a parallel article store: there is deliberately no
table holding one row per raw provider sighting, and nothing here can promote a candidate into
``articles``, classification, or matching.

Retention is bounded by policy: observation aggregates for 30 days, their bounded samples for
7 days, and benchmark evidence for 90 days. Sample expiry is shorter than aggregate expiry, so
pruning clears samples in place on rows that must survive.
"""

from datetime import date, timedelta
from enum import StrEnum
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from market_intelligence.discovery.models import (
    NonBlankString,
    ObservationStatus,
    SourceId,
    UtcDateTime,
)
from market_intelligence.source_registry import Domain

MAX_OBSERVATION_SAMPLES = 3
OBSERVATION_RETENTION_DAYS = 30
SAMPLE_RETENTION_DAYS = 7
BENCHMARK_EVIDENCE_RETENTION_DAYS = 90


class DiscoveryPersistenceModel(BaseModel):
    """Strict immutable models exchanged with the discovery persistence boundary."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class DiscoveryRecordOutcome(StrEnum):
    CREATED = "CREATED"
    UPDATED = "UPDATED"


class DiscoveryObservationKey(DiscoveryPersistenceModel):
    """The 4-tuple aggregate key used by every observation operation.

    ``domain`` is part of the key everywhere, including the sample cap: two domains sharing a
    query namespace must never compete for the same sample slots.
    """

    observation_day: date
    query_id: NonBlankString
    domain: Domain
    observation_status: ObservationStatus

    def label(self) -> str:
        """Return a sanitized key label safe to place in error messages and logs."""
        return (
            f"{self.observation_day.isoformat()}/{self.query_id}/"
            f"{self.domain.value}/{self.observation_status.value}"
        )


class DiscoveryObservationSample(DiscoveryPersistenceModel):
    """One bounded sample: locator only, never body text."""

    url: NonBlankString
    hostname: NonBlankString


class DiscoveryObservation(DiscoveryPersistenceModel):
    """One provider sighting to fold into its daily aggregate."""

    key: DiscoveryObservationKey
    observed_at: UtcDateTime
    sample: DiscoveryObservationSample | None = None


class DiscoveryObservationRecordResult(DiscoveryPersistenceModel):
    """State of the aggregate row after folding in one observation."""

    outcome: DiscoveryRecordOutcome
    observation_count: int = Field(ge=1)
    first_seen_at: UtcDateTime
    last_seen_at: UtcDateTime
    sample_count: int = Field(ge=0, le=MAX_OBSERVATION_SAMPLES)


class DiscoveryBenchmarkEvidence(DiscoveryPersistenceModel):
    """One sentinel article's evidence for one benchmark run.

    Repeated sightings of the same sentinel do not create rows; they only move
    ``gdelt_first_usable_seen_at`` earlier.

    ``gdelt_first_usable_seen_at`` means exactly: the earliest discovery sighting for this
    sentinel that has already passed discovery admission with
    ``ObservationStatus.ADMITTED``. It is not a raw provider observation time.

    Caller precondition: a caller may supply ``gdelt_first_usable_seen_at`` only for a
    sighting whose admission result is ``ADMITTED``. A sighting that resolved to ``UNKNOWN``,
    ``AMBIGUOUS_ROUTE``, ``RIGHTS_METADATA_DENIED``, or ``IDENTITY_INCOMPATIBLE`` must leave
    the field ``None``; an evidence row may legitimately exist with it still ``None``. The
    repository preserves the earliest usable timestamp but does not independently re-run
    admission, so persisting a raw sighting time here would make a later benchmark
    artificially optimistic.
    """

    benchmark_run_id: NonBlankString
    sentinel_article_key: NonBlankString
    source_id: SourceId
    query_id: NonBlankString
    evidence_day: date
    published_at: UtcDateTime | None = None
    direct_first_seen_at: UtcDateTime | None = None
    gdelt_first_usable_seen_at: UtcDateTime | None = None


class DiscoveryBenchmarkEvidenceRecordResult(DiscoveryPersistenceModel):
    """State of the evidence row after recording one sighting."""

    outcome: DiscoveryRecordOutcome
    published_at: UtcDateTime | None = None
    direct_first_seen_at: UtcDateTime | None = None
    gdelt_first_usable_seen_at: UtcDateTime | None = None


class DiscoveryRetentionCutoffs(DiscoveryPersistenceModel):
    """Exclusive day cutoffs for one pruning run; rows on a cutoff day are preserved."""

    observation_cutoff_day: date
    sample_cutoff_day: date
    evidence_cutoff_day: date

    @model_validator(mode="after")
    def validate_cutoff_order(self) -> Self:
        if self.sample_cutoff_day < self.observation_cutoff_day:
            raise ValueError(
                "sample_cutoff_day must not be earlier than observation_cutoff_day: "
                "samples expire before the aggregates that carry them"
            )
        return self

    @classmethod
    def for_date(cls, today: date) -> Self:
        """Build the policy cutoffs for a pruning run made on ``today``."""
        return cls(
            observation_cutoff_day=today - timedelta(days=OBSERVATION_RETENTION_DAYS),
            sample_cutoff_day=today - timedelta(days=SAMPLE_RETENTION_DAYS),
            evidence_cutoff_day=today - timedelta(days=BENCHMARK_EVIDENCE_RETENTION_DAYS),
        )


class DiscoveryPruneResult(DiscoveryPersistenceModel):
    """What one pruning run removed."""

    deleted_observations: int = Field(ge=0)
    cleared_samples: int = Field(ge=0)
    deleted_benchmark_evidence: int = Field(ge=0)


class DiscoveryPersistenceError(RuntimeError):
    """Sanitized failure that never includes candidate URLs, samples, or credentials."""

    def __init__(self, operation: str, aggregate_key: str) -> None:
        self.operation = operation
        self.aggregate_key = aggregate_key
        super().__init__(f"discovery persistence {operation} failed for {aggregate_key}")


class DiscoveryObservationRepository(Protocol):
    """Bounded aggregate boundary for discovery observations and benchmark evidence."""

    def record_observation(
        self,
        observation: DiscoveryObservation,
    ) -> DiscoveryObservationRecordResult:
        """Fold one sighting into its daily aggregate, preserving the first-seen timestamp."""
        ...

    def record_benchmark_evidence(
        self,
        evidence: DiscoveryBenchmarkEvidence,
    ) -> DiscoveryBenchmarkEvidenceRecordResult:
        """Record sentinel evidence, never replacing an earlier first-usable timestamp.

        The caller may populate ``evidence.gdelt_first_usable_seen_at`` only for a sighting
        whose discovery admission result is ``ADMITTED``. This boundary preserves the earliest
        usable timestamp but does not independently re-run admission.
        """
        ...

    def prune(self, cutoffs: DiscoveryRetentionCutoffs) -> DiscoveryPruneResult:
        """Delete rows past their retention window and clear expired samples in place."""
        ...
