import json
import math
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from market_intelligence.classification import (
    CLASSIFIER_VERSION,
    ClassificationUsage,
    ClassifiedArticle,
    ProviderClassificationOutput,
    Topic,
)
from market_intelligence.source_registry import Domain, Market


def provider_json(**overrides: object) -> str:
    payload: dict[str, object] = {
        "is_relevant": True,
        "markets": ["US"],
        "category": "FINANCE",
        "topics": ["INTEREST_RATES"],
        "confidence": 0.93,
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_valid_unsorted_values_are_normalized_without_rejection() -> None:
    output = ProviderClassificationOutput.model_validate_json(
        provider_json(
            markets=["CN", "EU", "VN", "US"],
            topics=["REGULATION", "AI", "BANKING"],
        )
    )

    assert output.markets == (Market.VN, Market.US, Market.EU, Market.CN)
    assert output.topics == (Topic.AI, Topic.BANKING, Topic.REGULATION)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("markets", ["US", "US"]),
        ("topics", ["AI", "AI"]),
    ],
)
def test_duplicate_controlled_values_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError, match="duplicates"):
        ProviderClassificationOutput.model_validate_json(provider_json(**{field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("markets", ["UK"]),
        ("category", "HEALTHCARE"),
        ("topics", ["CRYPTO"]),
    ],
)
def test_unsupported_taxonomy_codes_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ProviderClassificationOutput.model_validate_json(provider_json(**{field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("is_relevant", 1),
        ("is_relevant", "true"),
        ("markets", "US"),
        ("markets", [1]),
        ("category", 1),
        ("topics", "AI"),
        ("topics", [True]),
    ],
)
def test_provider_semantics_reject_invalid_types(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ProviderClassificationOutput.model_validate_json(provider_json(**{field: value}))


@pytest.mark.parametrize(
    "missing_field",
    ["is_relevant", "markets", "category", "topics", "confidence"],
)
def test_provider_semantics_require_every_field(missing_field: str) -> None:
    payload = json.loads(provider_json())
    del payload[missing_field]

    with pytest.raises(ValidationError):
        ProviderClassificationOutput.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "overrides",
    [
        {"markets": []},
        {"category": None},
        {"markets": ["VN", "US", "EU", "CN", "US"]},
        {"topics": ["AI", "BANKING", "INTEREST_RATES", "OIL_GAS", "REGULATION", "SEMICONDUCTORS"]},
    ],
)
def test_relevant_invariants_are_enforced(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ProviderClassificationOutput.model_validate_json(provider_json(**overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {"markets": ["US"], "category": None, "topics": []},
        {"markets": [], "category": "FINANCE", "topics": []},
        {"markets": [], "category": None, "topics": ["AI"]},
    ],
)
def test_irrelevant_result_requires_exact_empty_semantics(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ProviderClassificationOutput.model_validate_json(
            provider_json(is_relevant=False, **overrides)
        )


def test_valid_irrelevant_result_is_accepted() -> None:
    output = ProviderClassificationOutput.model_validate_json(
        provider_json(
            is_relevant=False,
            markets=[],
            category=None,
            topics=[],
            confidence=0.8,
        )
    )

    assert output.markets == ()
    assert output.category is None
    assert output.topics == ()


@pytest.mark.parametrize("confidence", [0, 1, 0.0, 1.0])
def test_real_installed_pydantic_accepts_numeric_confidence_boundaries(
    confidence: int | float,
) -> None:
    output = ProviderClassificationOutput.model_validate_json(provider_json(confidence=confidence))

    assert output.confidence == float(confidence)


@pytest.mark.parametrize("confidence", ["0", "1", "0.5", True, False])
def test_strict_confidence_rejects_numeric_strings_and_booleans(confidence: object) -> None:
    with pytest.raises(ValidationError):
        ProviderClassificationOutput.model_validate_json(provider_json(confidence=confidence))


@pytest.mark.parametrize("confidence", [math.nan, math.inf, -math.inf, -0.01, 1.01])
def test_confidence_rejects_non_finite_and_out_of_range_values(confidence: float) -> None:
    with pytest.raises(ValidationError):
        ProviderClassificationOutput(
            is_relevant=True,
            markets=(Market.US,),
            category=Domain.FINANCE,
            topics=(Topic.INTEREST_RATES,),
            confidence=confidence,
        )


@pytest.mark.parametrize(
    "extra_field",
    [
        "article_id",
        "classifier_version",
        "classified_at",
        "requested_model",
        "provider_model",
        "prompt_version",
        "taxonomy_version",
        "cost",
        "usage",
        "reasoning",
    ],
)
def test_provider_output_rejects_application_owned_or_unknown_fields(
    extra_field: str,
) -> None:
    with pytest.raises(ValidationError):
        ProviderClassificationOutput.model_validate_json(
            provider_json(**{extra_field: "model-controlled"})
        )


def test_shared_contract_contains_only_approved_fields_and_normalizes_utc() -> None:
    local_time = datetime(2026, 8, 15, 19, 0, tzinfo=timezone(timedelta(hours=7)))
    classified = ClassifiedArticle(
        article_id="article-1",
        classifier_version=CLASSIFIER_VERSION,
        is_relevant=True,
        markets=(Market.US,),
        category=Domain.FINANCE,
        topics=(Topic.INTEREST_RATES,),
        confidence=0.9,
        classified_at=local_time,
    )

    assert set(ClassifiedArticle.model_fields) == {
        "article_id",
        "classifier_version",
        "is_relevant",
        "markets",
        "category",
        "topics",
        "confidence",
        "classified_at",
    }
    assert classified.classified_at == datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def test_shared_contract_rejects_naive_timestamp_and_wrong_version_name() -> None:
    payload = {
        "article_id": "article-1",
        "classifier_version": CLASSIFIER_VERSION,
        "is_relevant": True,
        "markets": ["US"],
        "category": "FINANCE",
        "topics": [],
        "confidence": 0.9,
        "classified_at": "2026-08-15T12:00:00",
    }
    with pytest.raises(ValidationError):
        ClassifiedArticle.model_validate_json(json.dumps(payload))

    payload["classified_at"] = "2026-08-15T12:00:00Z"
    payload["classification_version"] = payload.pop("classifier_version")
    with pytest.raises(ValidationError):
        ClassifiedArticle.model_validate_json(json.dumps(payload))


def usage(**overrides: object) -> ClassificationUsage:
    payload: dict[str, object] = {
        "prompt_tokens": 10,
        "prompt_cache_hit_tokens": 4,
        "prompt_cache_miss_tokens": 6,
        "completion_tokens": 3,
        "total_tokens": 13,
    }
    payload.update(overrides)
    return ClassificationUsage.model_validate(payload)


def test_usage_addition_aggregates_all_observed_counts() -> None:
    aggregate = usage() + usage(
        prompt_tokens=5,
        prompt_cache_hit_tokens=1,
        prompt_cache_miss_tokens=4,
        completion_tokens=2,
        total_tokens=7,
    )

    assert aggregate == ClassificationUsage(
        prompt_tokens=15,
        prompt_cache_hit_tokens=5,
        prompt_cache_miss_tokens=10,
        completion_tokens=5,
        total_tokens=20,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"prompt_tokens": -1},
        {"prompt_tokens": "10"},
        {"prompt_tokens": True},
        {"prompt_tokens": 11},
        {"total_tokens": 12},
    ],
)
def test_usage_rejects_negative_coerced_or_inconsistent_values(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        usage(**overrides)
