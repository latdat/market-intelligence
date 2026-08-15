from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from market_intelligence.classification import (
    ClassificationError,
    ClassificationErrorCategory,
    ClassificationMethod,
    ClassificationUsage,
)
from market_intelligence.persistence import (
    ClassificationClaim,
    ClassificationFailure,
    ClassificationKey,
    ClassificationRecord,
    ClassificationStatus,
)
from market_intelligence.source_registry import Domain, Market

NOW = datetime(2026, 8, 16, 4, 0, tzinfo=UTC)
TOKEN = UUID("00000000-0000-0000-0000-000000000001")


def record_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "article_id": "article-1",
        "classifier_version": "classification-v1",
        "status": ClassificationStatus.RETRYABLE,
        "classification_method": None,
        "is_relevant": None,
        "markets": None,
        "category": None,
        "topics": None,
        "confidence": None,
        "classified_at": None,
        "requested_model": "deepseek-v4-flash",
        "provider_model": None,
        "prompt_version": "classification-prompt-v1",
        "taxonomy_version": "classification-taxonomy-v1",
        "provider_request_id": None,
        "system_fingerprint": None,
        "prompt_tokens": 0,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": Decimal("0"),
        "last_pricing_id": None,
        "last_pricing_window": None,
        "attempt_count": 0,
        "max_attempts": 3,
        "last_provider_attempts": None,
        "claim_token": None,
        "claimed_at": None,
        "lease_expires_at": None,
        "next_attempt_at": NOW,
        "last_error_category": None,
        "last_http_status": None,
        "last_error_retryable": None,
        "last_error_at": None,
        "quarantined_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return values


def test_retryable_record_requires_remaining_durable_invocation_budget() -> None:
    with pytest.raises(ValidationError, match="remaining invocation budget"):
        ClassificationRecord(**record_values(attempt_count=3))


def test_processing_record_becomes_a_fenced_claim() -> None:
    record = ClassificationRecord(
        **record_values(
            status=ClassificationStatus.PROCESSING,
            attempt_count=1,
            claim_token=TOKEN,
            claimed_at=NOW,
            lease_expires_at=NOW + timedelta(minutes=5),
            next_attempt_at=None,
        )
    )

    claim = ClassificationClaim.from_record(record)

    assert claim.key == ClassificationKey(
        article_id="article-1",
        classifier_version="classification-v1",
    )
    assert claim.claim_token == TOKEN
    assert claim.attempt_count == 1
    assert claim.max_attempts == 3


def test_non_processing_record_cannot_retain_claim_fields() -> None:
    with pytest.raises(ValidationError, match="cannot retain claim"):
        ClassificationRecord(**record_values(claim_token=TOKEN))


def test_non_success_record_cannot_retain_provider_result_lineage() -> None:
    with pytest.raises(ValidationError, match="provider result lineage"):
        ClassificationRecord(**record_values(provider_model="deepseek-provider-model"))


def test_succeeded_record_maps_to_the_shared_contract() -> None:
    record = ClassificationRecord(
        **record_values(
            status=ClassificationStatus.SUCCEEDED,
            classification_method=ClassificationMethod.DEEPSEEK,
            is_relevant=True,
            markets=(Market.US, Market.EU),
            category=Domain.FINANCE,
            topics=(),
            confidence=0.91,
            classified_at=NOW,
            provider_model="deepseek-provider-model",
            provider_request_id="request-1",
            system_fingerprint="fingerprint-1",
            attempt_count=1,
            last_provider_attempts=1,
            next_attempt_at=None,
        )
    )

    classified = record.to_classified_article()

    assert classified.article_id == "article-1"
    assert classified.classifier_version == "classification-v1"
    assert classified.markets == (Market.US, Market.EU)
    assert set(type(classified).model_fields) == {
        "article_id",
        "classifier_version",
        "is_relevant",
        "markets",
        "category",
        "topics",
        "confidence",
        "classified_at",
    }


def test_succeeded_record_cannot_retain_error_metadata() -> None:
    with pytest.raises(ValidationError, match="terminal error metadata"):
        ClassificationRecord(
            **record_values(
                status=ClassificationStatus.SUCCEEDED,
                classification_method=ClassificationMethod.DEEPSEEK,
                is_relevant=False,
                markets=(),
                category=None,
                topics=(),
                confidence=0.8,
                classified_at=NOW,
                attempt_count=1,
                last_provider_attempts=1,
                next_attempt_at=None,
                last_error_category="old-error",
            )
        )


def test_deterministic_success_requires_zero_provider_metadata() -> None:
    record = ClassificationRecord(
        **record_values(
            status=ClassificationStatus.SUCCEEDED,
            classification_method=ClassificationMethod.DETERMINISTIC,
            is_relevant=True,
            markets=(Market.US,),
            category=Domain.FINANCE,
            topics=(),
            confidence=0.9,
            classified_at=NOW,
            attempt_count=1,
            last_provider_attempts=0,
            next_attempt_at=None,
        )
    )
    assert record.classification_method is ClassificationMethod.DETERMINISTIC

    with pytest.raises(ValidationError, match="contains provider metadata"):
        ClassificationRecord(
            **record_values(
                status=ClassificationStatus.SUCCEEDED,
                classification_method=ClassificationMethod.DETERMINISTIC,
                is_relevant=True,
                markets=(Market.US,),
                category=Domain.FINANCE,
                topics=(),
                confidence=0.9,
                classified_at=NOW,
                provider_model="not-allowed",
                attempt_count=1,
                last_provider_attempts=0,
                next_attempt_at=None,
            )
        )


def test_non_success_cannot_retain_classification_method() -> None:
    with pytest.raises(ValidationError, match="classification_method"):
        ClassificationRecord(**record_values(classification_method=ClassificationMethod.DEEPSEEK))


def test_quarantined_record_requires_terminal_error() -> None:
    with pytest.raises(ValidationError, match="requires timestamp and error"):
        ClassificationRecord(
            **record_values(
                status=ClassificationStatus.QUARANTINED,
                attempt_count=1,
                next_attempt_at=None,
            )
        )


def test_failure_from_de008_error_preserves_independent_provider_attempts() -> None:
    usage = ClassificationUsage(
        prompt_tokens=8,
        prompt_cache_hit_tokens=3,
        prompt_cache_miss_tokens=5,
        completion_tokens=2,
        total_tokens=10,
    )
    error = ClassificationError(
        "article-1",
        category=ClassificationErrorCategory.TIMEOUT,
        retryable=True,
        provider_attempts=3,
        last_http_status=503,
        usage=usage,
        estimated_cost_usd=Decimal("0.000004"),
        pricing_id="deepseek-v4-flash-2026-08-15",
    )

    failure = ClassificationFailure.from_error(error)

    assert failure.provider_attempts == 3
    assert failure.usage == usage
    assert failure.estimated_cost_usd == Decimal("0.000004")
    assert failure.error_category == "timeout"


def test_persistence_models_reject_unknown_fields_and_invalid_versions() -> None:
    with pytest.raises(ValidationError):
        ClassificationKey(
            article_id="article-1",
            classifier_version="classification-v1",
            unexpected="value",
        )
    with pytest.raises(ValidationError):
        ClassificationKey(
            article_id="article-1",
            classifier_version="classification_version-v1",
        )
