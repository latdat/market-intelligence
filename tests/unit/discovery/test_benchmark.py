from datetime import UTC, datetime, timedelta

import pytest

from market_intelligence.discovery import (
    BenchmarkTier,
    DirectPathDisposition,
    GdeltDiscoveryRole,
    IdentityMode,
    ObservationStatus,
    SentinelObservation,
    capture_within_24h_rate,
    capture_within_60m_rate,
    discovery_delay_percentiles,
    duplicate_candidate_rate,
    evaluate_benchmark_tier,
    evaluate_direct_path_disposition,
    missed_after_24h,
    unknown_publisher_rate,
)

PUBLISHED_AT = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


def build_observation(
    key: str,
    *,
    published_at: datetime | None = PUBLISHED_AT,
    delay: timedelta | None = None,
    status: ObservationStatus | None = ObservationStatus.ADMITTED,
) -> SentinelObservation:
    first_seen_at = None
    if delay is not None:
        base = published_at if published_at is not None else PUBLISHED_AT
        first_seen_at = base + delay
    return SentinelObservation(
        sentinel_article_key=key,
        source_id="xx_editorial_fixture_source",
        query_id="us_finance",
        published_at=published_at,
        gdelt_first_seen_at=first_seen_at,
        gdelt_observation_status=status,
    )


@pytest.mark.parametrize(
    ("capture_60m", "capture_24h", "expected"),
    [
        (0.95, 0.99, BenchmarkTier.GREEN_A),
        (0.90, 0.97, BenchmarkTier.GREEN_A),
        (0.89, 0.99, BenchmarkTier.GREEN_B),
        (0.85, 0.95, BenchmarkTier.GREEN_B),
        (0.80, 0.94, BenchmarkTier.YELLOW),
        (0.75, 0.90, BenchmarkTier.YELLOW),
        (0.74, 0.99, BenchmarkTier.RED),
        (0.10, 0.20, BenchmarkTier.RED),
    ],
)
def test_benchmark_tier_thresholds(
    capture_60m: float,
    capture_24h: float,
    expected: BenchmarkTier,
) -> None:
    tier = evaluate_benchmark_tier(
        capture_60m=capture_60m,
        capture_24h=capture_24h,
        eligible_sample_size=100,
        completed_days=14,
    )

    assert tier is expected


def test_green_a_requires_both_capture_thresholds() -> None:
    tier = evaluate_benchmark_tier(
        capture_60m=0.99,
        capture_24h=0.96,
        eligible_sample_size=100,
        completed_days=30,
    )

    assert tier is BenchmarkTier.GREEN_B


@pytest.mark.parametrize("completed_days", [0, 7, 13])
def test_interim_read_before_day_14_is_always_insufficient_data(completed_days: int) -> None:
    """A day-7 interim read must never grade a cell, however good the metrics look."""
    tier = evaluate_benchmark_tier(
        capture_60m=1.0,
        capture_24h=1.0,
        eligible_sample_size=10_000,
        completed_days=completed_days,
    )

    assert tier is BenchmarkTier.INSUFFICIENT_DATA


def test_sample_size_gate_applies_after_the_completed_days_gate() -> None:
    assert (
        evaluate_benchmark_tier(
            capture_60m=1.0,
            capture_24h=1.0,
            eligible_sample_size=49,
            completed_days=14,
        )
        is BenchmarkTier.INSUFFICIENT_DATA
    )
    assert (
        evaluate_benchmark_tier(
            capture_60m=1.0,
            capture_24h=1.0,
            eligible_sample_size=50,
            completed_days=14,
        )
        is BenchmarkTier.GREEN_A
    )


def test_both_gates_are_checked_before_any_threshold_comparison() -> None:
    tier = evaluate_benchmark_tier(
        capture_60m=0.0,
        capture_24h=0.0,
        eligible_sample_size=1,
        completed_days=1,
    )

    assert tier is BenchmarkTier.INSUFFICIENT_DATA


@pytest.mark.parametrize(
    ("tier", "identity_mode", "expected_role", "expected_direct"),
    [
        (
            BenchmarkTier.GREEN_A,
            IdentityMode.CANONICAL_URL_FALLBACK,
            GdeltDiscoveryRole.PRIMARY,
            DirectPathDisposition.MAY_REDUCE_TO_SENTINEL,
        ),
        (
            BenchmarkTier.GREEN_B,
            IdentityMode.CANONICAL_URL_FALLBACK,
            GdeltDiscoveryRole.PRIMARY,
            DirectPathDisposition.MAY_REDUCE_TO_SENTINEL,
        ),
        (
            BenchmarkTier.GREEN_A,
            IdentityMode.NATIVE_SOURCE_ITEM_ID_REQUIRED,
            GdeltDiscoveryRole.DISCOVERY_ACCELERATOR,
            DirectPathDisposition.REQUIRED,
        ),
        (
            BenchmarkTier.GREEN_B,
            IdentityMode.NATIVE_SOURCE_ITEM_ID_REQUIRED,
            GdeltDiscoveryRole.DISCOVERY_ACCELERATOR,
            DirectPathDisposition.REQUIRED,
        ),
        (
            BenchmarkTier.YELLOW,
            IdentityMode.CANONICAL_URL_FALLBACK,
            GdeltDiscoveryRole.PARALLEL_DISCOVERY,
            DirectPathDisposition.REQUIRED,
        ),
        (
            BenchmarkTier.YELLOW,
            IdentityMode.NATIVE_SOURCE_ITEM_ID_REQUIRED,
            GdeltDiscoveryRole.PARALLEL_DISCOVERY,
            DirectPathDisposition.REQUIRED,
        ),
        (
            BenchmarkTier.RED,
            IdentityMode.CANONICAL_URL_FALLBACK,
            GdeltDiscoveryRole.SUPPLEMENTAL,
            DirectPathDisposition.REQUIRED_PRIMARY,
        ),
        (
            BenchmarkTier.RED,
            IdentityMode.NATIVE_SOURCE_ITEM_ID_REQUIRED,
            GdeltDiscoveryRole.SUPPLEMENTAL,
            DirectPathDisposition.REQUIRED_PRIMARY,
        ),
        (
            BenchmarkTier.INSUFFICIENT_DATA,
            IdentityMode.CANONICAL_URL_FALLBACK,
            GdeltDiscoveryRole.NO_CHANGE,
            DirectPathDisposition.UNCHANGED,
        ),
        (
            BenchmarkTier.INSUFFICIENT_DATA,
            IdentityMode.NATIVE_SOURCE_ITEM_ID_REQUIRED,
            GdeltDiscoveryRole.NO_CHANGE,
            DirectPathDisposition.UNCHANGED,
        ),
    ],
)
def test_direct_path_policy_table(
    tier: BenchmarkTier,
    identity_mode: IdentityMode,
    expected_role: GdeltDiscoveryRole,
    expected_direct: DirectPathDisposition,
) -> None:
    policy = evaluate_direct_path_disposition(tier, identity_mode)

    assert policy.gdelt_role is expected_role
    assert policy.direct_path is expected_direct


def test_native_source_item_id_never_retires_the_direct_path() -> None:
    for tier in BenchmarkTier:
        policy = evaluate_direct_path_disposition(
            tier,
            IdentityMode.NATIVE_SOURCE_ITEM_ID_REQUIRED,
        )

        assert policy.direct_path is not DirectPathDisposition.MAY_REDUCE_TO_SENTINEL


def test_capture_rates_count_only_usable_discovery() -> None:
    observations = (
        build_observation("fast", delay=timedelta(minutes=10)),
        build_observation("slow", delay=timedelta(hours=6)),
        build_observation(
            "unknown-publisher",
            delay=timedelta(minutes=5),
            status=ObservationStatus.UNKNOWN,
        ),
        build_observation(
            "identity-incompatible",
            delay=timedelta(minutes=5),
            status=ObservationStatus.IDENTITY_INCOMPATIBLE,
        ),
        build_observation("never-seen", delay=None, status=None),
    )

    assert capture_within_60m_rate(observations) == pytest.approx(0.2)
    assert capture_within_24h_rate(observations) == pytest.approx(0.4)
    assert missed_after_24h(observations) == 3


def test_sentinels_without_publication_time_are_excluded_from_metrics() -> None:
    observations = (
        build_observation("dated", delay=timedelta(minutes=10)),
        build_observation("undated", published_at=None, delay=timedelta(minutes=10)),
    )

    assert capture_within_60m_rate(observations) == pytest.approx(1.0)
    assert capture_within_24h_rate(observations) == pytest.approx(1.0)
    assert missed_after_24h(observations) == 0


def test_capture_rates_are_zero_without_eligible_sentinels() -> None:
    assert capture_within_60m_rate(()) == 0.0
    assert capture_within_24h_rate(()) == 0.0
    assert missed_after_24h(()) == 0


def test_discovery_delay_percentiles_use_nearest_rank() -> None:
    observations = tuple(
        build_observation(f"sentinel-{minutes}", delay=timedelta(minutes=minutes))
        for minutes in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
    )

    percentiles = discovery_delay_percentiles(observations)

    assert percentiles.p50_seconds == pytest.approx(300.0)
    assert percentiles.p95_seconds == pytest.approx(600.0)


def test_discovery_delay_percentiles_are_none_without_usable_capture() -> None:
    percentiles = discovery_delay_percentiles(
        (build_observation("never-seen", delay=None, status=None),)
    )

    assert percentiles.p50_seconds is None
    assert percentiles.p95_seconds is None


def test_unknown_publisher_rate() -> None:
    statuses = (
        ObservationStatus.UNKNOWN,
        ObservationStatus.UNKNOWN,
        ObservationStatus.ADMITTED,
        ObservationStatus.RIGHTS_METADATA_DENIED,
    )

    assert unknown_publisher_rate(statuses) == pytest.approx(0.5)
    assert unknown_publisher_rate(()) == 0.0


def test_duplicate_candidate_rate_compares_canonical_urls() -> None:
    urls = (
        "https://editorial.example.test/news/business/story-1",
        "HTTPS://Editorial.Example.Test/news/business/story-1?utm_source=gdelt",
        "https://editorial.example.test/news/business/story-2",
    )

    assert duplicate_candidate_rate(urls) == pytest.approx(1 / 3)
    assert duplicate_candidate_rate(()) == 0.0


def test_duplicate_candidate_rate_keeps_unusable_urls_distinct() -> None:
    urls = (
        "ftp://editorial.example.test/news/business/story-1",
        "https://editorial.example.test/news/business/story-1",
    )

    assert duplicate_candidate_rate(urls) == 0.0
