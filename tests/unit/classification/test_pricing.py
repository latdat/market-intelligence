from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from market_intelligence.classification import ClassificationUsage
from market_intelligence.classification.pricing import (
    PricingCatalog,
    PricingConfigurationError,
    load_pricing_catalog,
)

PRICING_PATH = Path("config/classification/deepseek_pricing.toml")
MODEL = "deepseek-v4-flash"


def observed_usage() -> ClassificationUsage:
    return ClassificationUsage(
        prompt_tokens=300,
        prompt_cache_hit_tokens=100,
        prompt_cache_miss_tokens=200,
        completion_tokens=50,
        total_tokens=350,
    )


def test_committed_official_pricing_config_loads_with_versioned_schedules() -> None:
    catalog = load_pricing_catalog(PRICING_PATH)

    assert [schedule.pricing_id for schedule in catalog.schedules] == [
        "deepseek-v4-flash-usd-2026-04-24",
        "deepseek-v4-flash-usd-2026-08-16",
    ]
    assert all(
        schedule.source_url == "https://api-docs.deepseek.com/quick_start/pricing/"
        for schedule in catalog.schedules
    )


def test_effective_date_boundary_selects_old_then_new_schedule() -> None:
    catalog = load_pricing_catalog(PRICING_PATH)

    old = catalog.estimate(
        MODEL,
        datetime(2026, 8, 16, 15, 59, 59, tzinfo=UTC),
        observed_usage(),
    )
    new = catalog.estimate(
        MODEL,
        datetime(2026, 8, 16, 16, 0, 0, tzinfo=UTC),
        observed_usage(),
    )

    assert old.pricing_id == "deepseek-v4-flash-usd-2026-04-24"
    assert old.pricing_window == "ALL_DAY"
    assert old.amount_usd == Decimal("0.00004228")
    assert new.pricing_id == "deepseek-v4-flash-usd-2026-08-16"
    assert new.pricing_window == "OFF_PEAK_10_24"
    assert new.amount_usd == Decimal("0.0000777")


@pytest.mark.parametrize(
    ("hour", "minute", "expected_window"),
    [
        (0, 59, "OFF_PEAK_00_01"),
        (1, 0, "PEAK_01_04"),
        (3, 59, "PEAK_01_04"),
        (4, 0, "OFF_PEAK_04_06"),
        (5, 59, "OFF_PEAK_04_06"),
        (6, 0, "PEAK_06_10"),
        (9, 59, "PEAK_06_10"),
        (10, 0, "OFF_PEAK_10_24"),
        (23, 59, "OFF_PEAK_10_24"),
    ],
)
def test_peak_off_peak_utc_boundaries(
    hour: int,
    minute: int,
    expected_window: str,
) -> None:
    estimate = load_pricing_catalog(PRICING_PATH).estimate(
        MODEL,
        datetime(2026, 8, 17, hour, minute, tzinfo=UTC),
        ClassificationUsage.zero(),
    )

    assert estimate.pricing_window == expected_window


def schedule_payload() -> dict[str, object]:
    return {
        "pricing_id": "pricing-v1",
        "model": MODEL,
        "currency": "USD",
        "source_url": "https://provider.example/pricing",
        "verified_at": "2026-08-15T00:00:00Z",
        "effective_from": "2026-08-15T00:00:00Z",
        "windows": [
            {
                "name": "ALL_DAY",
                "start_minute_utc": 0,
                "end_minute_utc": 1440,
                "input_cache_hit_usd_per_million": "0.1",
                "input_cache_miss_usd_per_million": "0.2",
                "output_usd_per_million": "0.3",
            }
        ],
    }


def test_pricing_windows_must_cover_day_without_gap() -> None:
    schedule = schedule_payload()
    windows = schedule["windows"]
    assert isinstance(windows, list)
    first_window = windows[0]
    assert isinstance(first_window, dict)
    first_window["end_minute_utc"] = 1000

    with pytest.raises(ValidationError, match="cover all 1440"):
        PricingCatalog.model_validate({"schedules": [schedule]})


def test_effective_schedules_must_not_overlap() -> None:
    first = schedule_payload()
    first["effective_until"] = "2026-09-01T00:00:00Z"
    second = schedule_payload()
    second["pricing_id"] = "pricing-v2"
    second["effective_from"] = "2026-08-31T00:00:00Z"

    with pytest.raises(ValidationError, match="overlap"):
        PricingCatalog.model_validate({"schedules": [first, second]})


def test_estimate_rejects_naive_time_unknown_model_and_uncovered_date() -> None:
    catalog = load_pricing_catalog(PRICING_PATH)

    with pytest.raises(ValueError, match="timezone"):
        catalog.estimate(MODEL, datetime(2026, 8, 17), ClassificationUsage.zero())
    with pytest.raises(PricingConfigurationError, match="expected one"):
        catalog.estimate(
            "unknown-model",
            datetime(2026, 8, 17, tzinfo=UTC),
            ClassificationUsage.zero(),
        )
    with pytest.raises(PricingConfigurationError, match="expected one"):
        catalog.estimate(
            MODEL,
            datetime(2026, 1, 1, tzinfo=UTC),
            ClassificationUsage.zero(),
        )


def test_missing_pricing_file_is_sanitized_with_path_context() -> None:
    missing = Path("config/classification/missing.toml")

    with pytest.raises(PricingConfigurationError, match="invalid pricing configuration"):
        load_pricing_catalog(missing)
