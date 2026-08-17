"""Benchmark policy v1: discovery capture metrics and the two independent policy axes.

Axis 1 (:func:`evaluate_benchmark_tier`) grades the discovery provider per query cell.
Axis 2 (:func:`evaluate_direct_path_disposition`) decides what may happen to the direct
acquisition path for one route. The two axes are independent by design; see
:func:`evaluate_direct_path_disposition` for the rule that keeps them separate.

All metrics here run against fixture data. There is no live benchmark run yet, and nothing in
this module performs network access.
"""

import math
from collections.abc import Sequence
from datetime import timedelta

from market_intelligence.discovery.models import (
    BenchmarkTier,
    DirectPathDisposition,
    DiscoveryModel,
    GdeltDiscoveryRole,
    IdentityMode,
    NonBlankString,
    ObservationStatus,
    SourceId,
    UtcDateTime,
)
from market_intelligence.normalization import ArticleNormalizationError, canonicalize_url

MINIMUM_COMPLETED_DAYS = 14
MINIMUM_ELIGIBLE_SAMPLE_SIZE = 50

_CAPTURE_60M = timedelta(minutes=60)
_CAPTURE_24H = timedelta(hours=24)


class SentinelObservation(DiscoveryModel):
    """One sentinel article and how the discovery provider did or did not see it.

    ``gdelt_observation_status`` is ``None`` when the provider never returned the article at
    all. ``published_at`` is ``None`` when the publisher gave no usable publication timestamp;
    such sentinels are excluded from every latency and capture metric, and retrieval time is
    never substituted for it.

    ``gdelt_first_seen_at`` is the raw sighting time of an ephemeral benchmark input, qualified
    by the separate ``gdelt_observation_status`` field: the pair, not the timestamp alone,
    decides usability. It is deliberately not the same concept as the durable
    ``DiscoveryBenchmarkEvidence.gdelt_first_usable_seen_at``, which stores only an
    already-ADMITTED timestamp because it carries no status of its own.
    """

    sentinel_article_key: NonBlankString
    source_id: SourceId
    query_id: NonBlankString
    published_at: UtcDateTime | None = None
    direct_first_seen_at: UtcDateTime | None = None
    gdelt_first_seen_at: UtcDateTime | None = None
    gdelt_observation_status: ObservationStatus | None = None


class DiscoveryDelayPercentiles(DiscoveryModel):
    """Nearest-rank P50/P95 discovery delay, in seconds, or ``None`` when no usable capture."""

    p50_seconds: float | None = None
    p95_seconds: float | None = None


class DirectPathPolicy(DiscoveryModel):
    """Axis-1 provider role and axis-2 direct-path disposition for one route."""

    tier: BenchmarkTier
    identity_mode: IdentityMode
    gdelt_role: GdeltDiscoveryRole
    direct_path: DirectPathDisposition


def _is_usable_capture(observation: SentinelObservation) -> bool:
    """Usable discovery means route-resolved AND admission-compatible.

    A sighting whose publisher was ``UNKNOWN``, ambiguous, rights-denied, or
    identity-incompatible is not a successful capture, however fast it arrived.
    """
    return (
        observation.gdelt_observation_status is ObservationStatus.ADMITTED
        and observation.gdelt_first_seen_at is not None
    )


def _eligible(observations: Sequence[SentinelObservation]) -> list[SentinelObservation]:
    return [observation for observation in observations if observation.published_at is not None]


def _capture_delay(observation: SentinelObservation) -> timedelta | None:
    published_at = observation.published_at
    first_seen_at = observation.gdelt_first_seen_at
    if published_at is None or first_seen_at is None or not _is_usable_capture(observation):
        return None
    return first_seen_at - published_at


def _capture_rate(observations: Sequence[SentinelObservation], window: timedelta) -> float:
    eligible = _eligible(observations)
    if not eligible:
        return 0.0
    captured = sum(
        1
        for observation in eligible
        if (delay := _capture_delay(observation)) is not None and delay <= window
    )
    return captured / len(eligible)


def capture_within_60m_rate(observations: Sequence[SentinelObservation]) -> float:
    """Share of eligible sentinels usably captured within 60 minutes of publication."""
    return _capture_rate(observations, _CAPTURE_60M)


def capture_within_24h_rate(observations: Sequence[SentinelObservation]) -> float:
    """Share of eligible sentinels usably captured within 24 hours of publication."""
    return _capture_rate(observations, _CAPTURE_24H)


def missed_after_24h(observations: Sequence[SentinelObservation]) -> int:
    """Count eligible sentinels never usably captured within 24 hours of publication."""
    eligible = _eligible(observations)
    return sum(
        1
        for observation in eligible
        if (delay := _capture_delay(observation)) is None or delay > _CAPTURE_24H
    )


def discovery_delay_percentiles(
    observations: Sequence[SentinelObservation],
) -> DiscoveryDelayPercentiles:
    """Nearest-rank P50/P95 of usable capture delay across eligible sentinels."""
    delays = sorted(
        delay.total_seconds()
        for observation in _eligible(observations)
        if (delay := _capture_delay(observation)) is not None
    )
    if not delays:
        return DiscoveryDelayPercentiles()
    return DiscoveryDelayPercentiles(
        p50_seconds=_nearest_rank(delays, 0.50),
        p95_seconds=_nearest_rank(delays, 0.95),
    )


def _nearest_rank(sorted_values: Sequence[float], percentile: float) -> float:
    rank = max(1, math.ceil(percentile * len(sorted_values)))
    return sorted_values[rank - 1]


def unknown_publisher_rate(statuses: Sequence[ObservationStatus]) -> float:
    """Share of observations whose publisher did not resolve to any reviewed route."""
    if not statuses:
        return 0.0
    unknown = sum(1 for status in statuses if status is ObservationStatus.UNKNOWN)
    return unknown / len(statuses)


def duplicate_candidate_rate(candidate_urls: Sequence[str]) -> float:
    """Share of sightings that repeat a URL already seen in the same set.

    URLs are compared in canonical form where they can be canonicalized, and verbatim where
    they cannot, so a malformed provider URL is never silently merged with a valid one.
    """
    if not candidate_urls:
        return 0.0
    seen: set[str] = set()
    for url in candidate_urls:
        try:
            seen.add(canonicalize_url(url))
        except ArticleNormalizationError:
            seen.add(url)
    return (len(candidate_urls) - len(seen)) / len(candidate_urls)


def evaluate_benchmark_tier(
    capture_60m: float,
    capture_24h: float,
    eligible_sample_size: int,
    completed_days: int,
) -> BenchmarkTier:
    """Grade one query cell against benchmark policy v1.

    The production decision window is 14 completed days. ``completed_days`` is checked first
    and ``eligible_sample_size`` second, both before any threshold comparison, so an interim
    read (for example at day 7) can only ever return ``INSUFFICIENT_DATA`` however good the
    metrics look. Such a tier is informational and must never drive a production strategy
    change. After the two gating checks, thresholds are evaluated top to bottom and the first
    matching tier wins.
    """
    if completed_days < MINIMUM_COMPLETED_DAYS:
        return BenchmarkTier.INSUFFICIENT_DATA
    if eligible_sample_size < MINIMUM_ELIGIBLE_SAMPLE_SIZE:
        return BenchmarkTier.INSUFFICIENT_DATA
    if capture_60m >= 0.90 and capture_24h >= 0.97:
        return BenchmarkTier.GREEN_A
    if capture_60m >= 0.85 and capture_24h >= 0.95:
        return BenchmarkTier.GREEN_B
    if capture_60m >= 0.75 and capture_24h >= 0.90:
        return BenchmarkTier.YELLOW
    return BenchmarkTier.RED


def evaluate_direct_path_disposition(
    tier: BenchmarkTier,
    identity_mode: IdentityMode,
) -> DirectPathPolicy:
    """Combine the two independent benchmark-policy axes for one route.

    The benchmark tier determines the discovery provider's role. ``identity_mode``
    independently determines whether the direct acquisition path may be retired. A
    ``NATIVE_SOURCE_ITEM_ID_REQUIRED`` route never becomes eligible to retire its direct path
    purely from a good benchmark score: without a proven publisher-native ID, discovery can
    only ever be a signal for that route, so its direct connector remains a required
    production dependency unconditionally.
    """
    if tier is BenchmarkTier.INSUFFICIENT_DATA:
        return DirectPathPolicy(
            tier=tier,
            identity_mode=identity_mode,
            gdelt_role=GdeltDiscoveryRole.NO_CHANGE,
            direct_path=DirectPathDisposition.UNCHANGED,
        )
    if tier in {BenchmarkTier.GREEN_A, BenchmarkTier.GREEN_B}:
        if identity_mode is IdentityMode.CANONICAL_URL_FALLBACK:
            return DirectPathPolicy(
                tier=tier,
                identity_mode=identity_mode,
                gdelt_role=GdeltDiscoveryRole.PRIMARY,
                direct_path=DirectPathDisposition.MAY_REDUCE_TO_SENTINEL,
            )
        return DirectPathPolicy(
            tier=tier,
            identity_mode=identity_mode,
            gdelt_role=GdeltDiscoveryRole.DISCOVERY_ACCELERATOR,
            direct_path=DirectPathDisposition.REQUIRED,
        )
    if tier is BenchmarkTier.YELLOW:
        return DirectPathPolicy(
            tier=tier,
            identity_mode=identity_mode,
            gdelt_role=GdeltDiscoveryRole.PARALLEL_DISCOVERY,
            direct_path=DirectPathDisposition.REQUIRED,
        )
    return DirectPathPolicy(
        tier=tier,
        identity_mode=identity_mode,
        gdelt_role=GdeltDiscoveryRole.SUPPLEMENTAL,
        direct_path=DirectPathDisposition.REQUIRED_PRIMARY,
    )
