"""Offline routing tests for classification-v2."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from market_intelligence.articles import CanonicalArticle
from market_intelligence.classification import (
    ClassificationError,
    ClassificationErrorCategory,
    ClassificationMethod,
    ClassificationResult,
    ClassificationUsage,
    ClassifiedArticle,
    DeterministicClassifier,
    HybridArticleClassifier,
    Topic,
    load_deterministic_rules,
)
from market_intelligence.source_registry import Domain, Market, SourceConfig

NOW = datetime(2026, 8, 15, tzinfo=UTC)


def source(*, ai_allowed: bool) -> SourceConfig:
    return SourceConfig.model_validate(
        {
            "source_id": "test_source",
            "name": "Test Source",
            "market": "US",
            "language": "en",
            "source_type": "REGULATOR",
            "authority_level": "PRIMARY",
            "domains": ["FINANCE", "LAW_POLICY"],
            "content_scope": "FORMAL_REGULATORY_LEGAL",
            "acquisition": {
                "method": "RSS",
                "endpoint_url": "https://example.test/feed",
                "poll_interval_minutes": 15,
            },
            "rights": {
                "can_fetch": True,
                "can_store_metadata": True,
                "can_store_full_text": False,
                "can_ai_process": ai_allowed,
                "can_show_snippet": True,
                "can_redistribute_full_text": False,
                "rights_review_status": "APPROVED" if ai_allowed else "PENDING",
            },
            "cost": {"type": "FREE", "monthly_fixed_usd": 0},
            "priority": 100,
        }
    )


def article(title: str) -> CanonicalArticle:
    return CanonicalArticle(
        article_id="article-1",
        source_id="test_source",
        url="https://example.test/article",
        canonical_url="https://example.test/article",
        title=title,
        language="en",
        market=Market.US,
        discovered_at=NOW,
        content_hash="hash",
    )


def deepseek_result() -> ClassificationResult:
    return ClassificationResult(
        classified_article=ClassifiedArticle(
            article_id="article-1",
            classifier_version="classification-v1",
            is_relevant=True,
            markets=(Market.US,),
            category=Domain.FINANCE,
            topics=(Topic.BANKING,),
            confidence=0.82,
            classified_at=NOW,
        ),
        classification_method=ClassificationMethod.DEEPSEEK,
        requested_model="deepseek-v4-flash",
        provider_model="provider-model",
        prompt_version="classification-prompt-v1",
        taxonomy_version="classification-taxonomy-v1",
        usage=ClassificationUsage(
            prompt_tokens=8,
            prompt_cache_hit_tokens=3,
            prompt_cache_miss_tokens=5,
            completion_tokens=2,
            total_tokens=10,
        ),
        estimated_cost_usd=Decimal("0.000004"),
        pricing_id="pricing-v1",
        pricing_window="off_peak",
        duration_ms=25,
        provider_attempts=1,
        provider_request_id="request-1",
        system_fingerprint="fingerprint-1",
    )


class FakeDeepSeek:
    def __init__(self) -> None:
        self.calls = 0

    async def classify(
        self,
        candidate: CanonicalArticle,
        configured_source: SourceConfig,
    ) -> ClassificationResult:
        del candidate, configured_source
        self.calls += 1
        return deepseek_result()


def hybrid(fake: FakeDeepSeek) -> HybridArticleClassifier:
    deterministic = DeterministicClassifier(
        load_deterministic_rules(Path("config/classification/deterministic_rules.toml"))
    )
    return HybridArticleClassifier(
        deterministic,
        fake,
        clock=lambda: NOW,
        monotonic=lambda: 1.0,
    )


def test_confident_result_makes_zero_deepseek_calls_and_zero_usage() -> None:
    fake = FakeDeepSeek()

    result = asyncio.run(
        hybrid(fake).classify(
            article("Federal Reserve interest rate decision"),
            source(ai_allowed=False),
        )
    )

    assert fake.calls == 0
    assert result.classification_method is ClassificationMethod.DETERMINISTIC
    assert result.classified_article.classifier_version == "classification-v2"
    assert result.provider_attempts == 0
    assert result.usage == ClassificationUsage.zero()
    assert result.estimated_cost_usd == 0
    assert result.provider_model is None
    assert result.pricing_id is None


def test_ambiguous_with_approved_rights_invokes_de008_once_and_preserves_cost() -> None:
    fake = FakeDeepSeek()

    result = asyncio.run(
        hybrid(fake).classify(article("General announcement"), source(ai_allowed=True))
    )

    assert fake.calls == 1
    assert result.classification_method is ClassificationMethod.DEEPSEEK
    assert result.classified_article.classifier_version == "classification-v2"
    assert result.provider_attempts == 1
    assert result.usage.total_tokens == 10
    assert result.estimated_cost_usd == Decimal("0.000004")


def test_ambiguous_without_ai_rights_is_terminal_without_provider_call() -> None:
    fake = FakeDeepSeek()

    with pytest.raises(ClassificationError) as raised:
        asyncio.run(
            hybrid(fake).classify(article("General announcement"), source(ai_allowed=False))
        )

    assert raised.value.category is ClassificationErrorCategory.AI_FALLBACK_NOT_ALLOWED
    assert raised.value.retryable is False
    assert raised.value.provider_attempts == 0
    assert fake.calls == 0
