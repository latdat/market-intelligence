"""DE-009C deterministic-first classifier with rights-gated DeepSeek fallback."""

import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from market_intelligence.articles import CanonicalArticle
from market_intelligence.classification.classifier import (
    ArticleClassifier,
    ClassificationError,
    ClassificationErrorCategory,
    validate_classification_rights,
)
from market_intelligence.classification.deterministic import (
    DeterministicClassifier,
    DeterministicDisposition,
)
from market_intelligence.classification.models import (
    HYBRID_CLASSIFIER_VERSION,
    PROMPT_VERSION,
    TAXONOMY_VERSION,
    ClassificationMethod,
    ClassificationResult,
    ClassificationUsage,
    ClassifiedArticle,
)
from market_intelligence.source_registry import SourceConfig

DEEPSEEK_FALLBACK_MODEL = "deepseek-v4-flash"

type Clock = Callable[[], datetime]
type MonotonicClock = Callable[[], float]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class HybridArticleClassifier:
    """Route confident local results to success and ambiguous work to DeepSeek."""

    def __init__(
        self,
        deterministic: DeterministicClassifier,
        deepseek: ArticleClassifier,
        *,
        clock: Clock = _utc_now,
        monotonic: MonotonicClock = time.perf_counter,
    ) -> None:
        self._deterministic = deterministic
        self._deepseek = deepseek
        self._clock = clock
        self._monotonic = monotonic

    async def classify(
        self,
        article: CanonicalArticle,
        source: SourceConfig,
    ) -> ClassificationResult:
        started_at = self._monotonic()
        try:
            deterministic = self._deterministic.classify(article, source)
        except ValueError as error:
            raise ClassificationError(
                article.article_id,
                category=ClassificationErrorCategory.INVALID_INPUT,
                retryable=False,
                provider_attempts=0,
            ) from error

        if deterministic.disposition is DeterministicDisposition.CONFIDENT:
            assert deterministic.category is not None
            assert deterministic.confidence is not None
            return ClassificationResult(
                classified_article=ClassifiedArticle(
                    article_id=article.article_id,
                    classifier_version=HYBRID_CLASSIFIER_VERSION,
                    is_relevant=True,
                    markets=deterministic.markets,
                    category=deterministic.category,
                    topics=deterministic.topics,
                    confidence=deterministic.confidence,
                    classified_at=self._clock(),
                ),
                classification_method=ClassificationMethod.DETERMINISTIC,
                requested_model=DEEPSEEK_FALLBACK_MODEL,
                provider_model=None,
                prompt_version=PROMPT_VERSION,
                taxonomy_version=TAXONOMY_VERSION,
                usage=ClassificationUsage.zero(),
                estimated_cost_usd=Decimal(0),
                pricing_id=None,
                pricing_window=None,
                duration_ms=max(0, round((self._monotonic() - started_at) * 1_000)),
                provider_attempts=0,
                provider_request_id=None,
                system_fingerprint=None,
            )

        try:
            validate_classification_rights(article, source)
        except ClassificationError as error:
            if error.category is not ClassificationErrorCategory.RIGHTS_DENIED:
                raise
            raise ClassificationError(
                article.article_id,
                category=ClassificationErrorCategory.AI_FALLBACK_NOT_ALLOWED,
                retryable=False,
                provider_attempts=0,
            ) from error

        fallback = await self._deepseek.classify(article, source)
        hybrid_article = fallback.classified_article.model_copy(
            update={"classifier_version": HYBRID_CLASSIFIER_VERSION}
        )
        return fallback.model_copy(
            update={
                "classified_article": hybrid_article,
                "classification_method": ClassificationMethod.DEEPSEEK,
                "duration_ms": max(0, round((self._monotonic() - started_at) * 1_000)),
            }
        )
